from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from modules.db import get_connection, initialize_database, record_inventory_adjustment
from modules.import_excel import analyze_snapshot_import, DEFAULT_COLUMN_MAP, import_snapshot, read_excel_preview, recent_import_runs, summarize_import
from modules.reference_import import analyze_reference_html, analyze_reference_pdf, import_reference_html, import_reference_pdf, import_reference_rows, import_reference_url, parse_reference_pdf, repair_compatibility_model_links
from modules.reference_search import compatibility_reference_summary, compatibility_source_summary, inventory_model_priority_matrix, search_inventory_detected_models, search_reference_parts, search_related_compatibility, search_system_models
from modules.search import recent_transactions, search_inventory, search_inventory_records, transactions_for_import_run


st.set_page_config(page_title="FieldStock", page_icon="F", layout="wide")


initialize_database()


def default_adjustment_reference() -> str:
    return f"adjustment-{datetime.now().strftime('%Y%m%d-%H%M%S')}"


def reset_adjustment_form_state() -> None:
    st.session_state["adjustment_action"] = "Use stock"
    st.session_state["adjustment_quantity"] = 1.0
    st.session_state["adjustment_reference"] = default_adjustment_reference()
    st.session_state["adjustment_notes"] = ""


if "adjustment_action" not in st.session_state:
    st.session_state["adjustment_action"] = "Use stock"

if "adjustment_quantity" not in st.session_state:
    st.session_state["adjustment_quantity"] = 1.0

if "adjustment_reference" not in st.session_state:
    st.session_state["adjustment_reference"] = default_adjustment_reference()

if "adjustment_notes" not in st.session_state:
    st.session_state["adjustment_notes"] = ""

if st.session_state.pop("reset_adjustment_form", False):
    reset_adjustment_form_state()

st.title("FieldStock")
st.caption("Local inventory and compatibility assistant")

with st.sidebar:
    st.header("Import Settings")
    operator_name = st.text_input("Operator name", value="system")
    reference = st.text_input(
        "Import reference",
        value=f"snapshot-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
    )
    st.info("This MVP supports snapshot imports first. Delta imports can be added once the stock workflow is stable.")

import_tab, search_tab, compatibility_tab, adjustment_tab = st.tabs(["Import", "Inventory Search", "Compatibility Search", "Stock Adjustment"])

with import_tab:
    st.subheader("Excel Snapshot Import")
    st.write("Upload an ERP or inventory export, confirm the column mapping, preview the data, then apply a snapshot import.")

    uploaded_file = st.file_uploader("Inventory Excel file", type=["xlsx", "xlsm", "xls"])

    column_labels = {"manufacturer": "OEM"}
    column_map = {}
    for key, default_value in DEFAULT_COLUMN_MAP.items():
        label = column_labels.get(key, key.replace("_", " ").title())
        column_map[key] = st.text_input(label, value=default_value)

    if uploaded_file is not None:
        file_bytes = uploaded_file.getvalue()
        try:
            preview_df, source_columns = read_excel_preview(file_bytes)
            summary = summarize_import(file_bytes, column_map)
            with get_connection() as conn:
                impact = analyze_snapshot_import(conn, file_bytes, column_map)
        except Exception as exc:
            st.error(f"Unable to read the uploaded file: {exc}")
        else:
            metric_columns = st.columns(4)
            metric_columns[0].metric("Rows", summary["rows"])
            metric_columns[1].metric("Unique Parts", summary["unique_parts"])
            metric_columns[2].metric("Locations", summary["locations"])
            metric_columns[3].metric("Total Quantity", f"{summary['total_quantity']:.2f}")

            impact_columns = st.columns(5)
            impact_columns[0].metric("Rows To Import", impact.rows_to_import)
            impact_columns[1].metric("New Balances", impact.new_balances)
            impact_columns[2].metric("Updated Balances", impact.updated_balances)
            impact_columns[3].metric("Unchanged Balances", impact.unchanged_balances)
            impact_columns[4].metric("Balances To Zero", impact.balances_to_zero)

            st.caption(f"Import reference: {reference.strip() or 'snapshot-import'}")
            st.caption(f"Preview generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

            if impact.balances_to_zero > 0:
                st.warning(
                    f"This snapshot will zero {impact.balances_to_zero} existing non-zero balance(s) that are missing from the file."
                )

            st.write("Source columns detected:")
            st.write(pd.DataFrame({"column_name": source_columns}))

            st.write("Preview:")
            st.dataframe(preview_df, use_container_width=True)

            if impact.balance_change_preview:
                st.write("Expected balance changes:")
                st.dataframe(pd.DataFrame(impact.balance_change_preview).head(25), use_container_width=True)

            if impact.zero_balance_preview:
                st.write("Balances that will be zeroed:")
                st.dataframe(pd.DataFrame(impact.zero_balance_preview).head(25), use_container_width=True)

            if st.button("Apply Snapshot Import", type="primary"):
                try:
                    with get_connection() as conn:
                        result = import_snapshot(
                            conn=conn,
                            file_bytes=file_bytes,
                            column_map=column_map,
                            created_by=operator_name.strip() or "system",
                            reference=reference.strip() or "snapshot-import",
                            source_filename=uploaded_file.name,
                        )
                except Exception as exc:
                    st.error(f"Import failed: {exc}")
                else:
                    st.success(
                        "Snapshot import completed. "
                        f"Imported {result.rows_imported} rows and zeroed {result.balances_zeroed} balances missing from the file."
                    )

    st.subheader("Recent Import Runs")
    import_filter_columns = st.columns([2, 1, 1])
    import_reference_filter = import_filter_columns[0].text_input(
        "Filter by reference",
        placeholder="Search import reference",
        key="import_reference_filter",
    )
    use_import_date_filter = import_filter_columns[1].checkbox(
        "Use date filter",
        value=False,
        key="use_import_date_filter",
    )
    import_limit = int(
        import_filter_columns[2].selectbox(
            "Rows",
            options=[10, 20, 50, 100],
            index=1,
            key="import_runs_limit",
        )
    )

    import_date_from = None
    import_date_to = None
    if use_import_date_filter:
        date_columns = st.columns(2)
        import_date_from = date_columns[0].date_input(
            "From date",
            value=datetime.now().date() - timedelta(days=30),
            key="import_date_from",
        )
        import_date_to = date_columns[1].date_input(
            "To date",
            value=datetime.now().date(),
            key="import_date_to",
        )

    with get_connection() as conn:
        import_runs = recent_import_runs(
            conn,
            limit=import_limit,
            reference_query=import_reference_filter,
            created_at_from=import_date_from,
            created_at_to=import_date_to,
        )

    if import_runs:
        import_runs_df = pd.DataFrame([dict(row) for row in import_runs])
        st.dataframe(import_runs_df, use_container_width=True)

        import_run_options = import_runs_df.to_dict("records")

        def format_import_run_option(option: dict[str, object]) -> str:
            return (
                f"{option['created_at']} | {option['reference']} | "
                f"rows {int(option['rows_imported'])} | zeroed {int(option['balances_zeroed'])}"
            )

        selected_import_run = st.selectbox(
            "Inspect import run",
            options=import_run_options,
            format_func=format_import_run_option,
            key="selected_import_run_reference",
        )

        with get_connection() as conn:
            import_run_transactions = transactions_for_import_run(conn, str(selected_import_run["reference"]))

        st.write("Transactions for selected import run:")
        if import_run_transactions:
            st.dataframe(
                pd.DataFrame([dict(row) for row in import_run_transactions]).rename(columns={"oem": "OEM"}),
                use_container_width=True,
            )
        else:
            st.info("No inventory transactions were found for the selected import run.")
    else:
        st.info("No snapshot imports matched the current filters.")

with search_tab:
    st.subheader("Inventory Search")
    query = st.text_input("Search by part number, alias, or description")
    available_only = st.checkbox("Available stock only", value=True)

    with get_connection() as conn:
        rows = search_inventory(
            conn,
            query=query,
            available_only=available_only,
            limit=200,
            include_reference_aliases=True,
        )
        transactions = recent_transactions(conn)

    if rows:
        search_results_df = pd.DataFrame([dict(row) for row in rows]).rename(columns={"oem": "OEM"})
        st.dataframe(search_results_df, use_container_width=True)
    else:
        st.info("No inventory rows matched the current search.")

    st.subheader("Recent Transactions")
    if transactions:
        st.dataframe(pd.DataFrame([dict(row) for row in transactions]), use_container_width=True)
    else:
        st.info("No transactions recorded yet.")

with compatibility_tab:
    st.subheader("Compatibility Reference Search")
    st.write("Build a local model-to-part reference from supplier pages, then cross-check compatible parts against your inventory.")

    st.markdown("#### Long-Term Priority Matrix")
    priority_columns = st.columns([2, 1])
    priority_brand_filter = priority_columns[0].selectbox(
        "Vendor focus",
        options=["All", "HP/HPE", "DELL"],
        index=0,
        key="priority_brand_filter",
    )
    priority_limit = int(
        priority_columns[1].selectbox(
            "Rows",
            options=[5, 10, 20],
            index=1,
            key="priority_limit",
        )
    )

    with get_connection() as conn:
        priority_rows = inventory_model_priority_matrix(
            conn,
            brand_filter="" if priority_brand_filter == "All" else priority_brand_filter,
            limit=priority_limit,
        )

    with get_connection() as conn:
        reference_summary = compatibility_reference_summary(conn)
        source_rows = compatibility_source_summary(conn)

    summary_columns = st.columns(4)
    summary_columns[0].metric("System Models", int(reference_summary["system_models"]))
    summary_columns[1].metric("Reference Parts", int(reference_summary["reference_parts"]))
    summary_columns[2].metric("Compatibilities", int(reference_summary["compatibilities"]))
    summary_columns[3].metric("Sources", int(reference_summary["sources"]))

    st.markdown("#### Imported Sources")
    if source_rows:
        source_df = pd.DataFrame(
            [
                {
                    "source_name": row["source_name"],
                    "source_type": row["source_type"],
                    "source_url": row["source_url"],
                    "reference_parts": int(row["reference_part_count"]),
                    "system_models": int(row["system_model_count"]),
                    "compatibilities": int(row["compatibility_count"]),
                    "manufacturers": row["manufacturers"] or "Unknown",
                    "updated_at": row["updated_at"],
                }
                for row in source_rows
            ]
        )
        st.dataframe(source_df, use_container_width=True)
    else:
        st.info("No compatibility reference sources have been imported yet.")

    if int(reference_summary["system_models"]) == 0:
        st.warning(
            "No compatibility reference data has been imported yet. The matching system-model list stays empty until you import a saved HTML listing or a manual CSV/Excel reference file."
        )

    st.markdown("#### Compatibility Maintenance")
    st.caption("Repair older imported compatibility rows after parser improvements such as Gen8 to G8 normalization.")
    if st.button("Repair existing compatibility model links", key="repair_compatibility_model_links"):
        try:
            with get_connection() as conn:
                repair_result = repair_compatibility_model_links(conn)
        except Exception as exc:
            st.error(f"Compatibility repair failed: {exc}")
        else:
            st.success(
                "Compatibility repair completed. "
                f"Scanned {repair_result.compatibilities_scanned} links, added {repair_result.models_upserted} model(s), "
                f"and created {repair_result.compatibilities_upserted} compatibility link(s)."
            )

    if priority_rows:
        priority_df = pd.DataFrame(priority_rows)
        st.dataframe(
            priority_df[
                [
                    "brand",
                    "model",
                    "family",
                    "priority_score",
                    "inventory_mentions",
                    "distinct_parts",
                    "category_diversity",
                    "family_coverage",
                    "longevity_score",
                ]
            ],
            use_container_width=True,
        )

        top_priority = priority_rows[:5]
        st.caption(
            "Priority score blends current inventory mentions, distinct part coverage, category diversity, model-family breadth, and a simple longevity heuristic."
        )
        st.write("Suggested first models to build out:")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "brand": row["brand"],
                        "model": row["model"],
                        "priority_score": row["priority_score"],
                        "inventory_mentions": row["inventory_mentions"],
                        "example_part": row["examples"][0]["part_number"] if row["examples"] else "",
                    }
                    for row in top_priority
                ]
            ),
            use_container_width=True,
        )
    else:
        st.info("No HP/HPE or Dell system models were detected in current inventory descriptions.")

    st.markdown("#### Import From Website")
    website_columns = st.columns([3, 2])
    reference_url = website_columns[0].text_input(
        "Listing URL",
        value="https://www.harddrivesdirect.com/HTML_HP_SAS_SATA_1.php",
        key="compatibility_reference_url",
    )
    reference_source_name = website_columns[1].text_input(
        "Source label",
        value="HardDrivesDirect",
        key="compatibility_source_name",
    )

    if st.button("Fetch and import compatibility page", key="import_compatibility_page"):
        try:
            with get_connection() as conn:
                result = import_reference_url(
                    conn,
                    url=reference_url.strip(),
                    source_name=reference_source_name.strip() or "HardDrivesDirect",
                )
        except Exception as exc:
            st.error(f"Compatibility import failed: {exc}")
        else:
            st.success(
                "Compatibility import completed. "
                f"Parsed {result.rows_seen} rows, added {result.models_upserted} models, "
                f"{result.parts_upserted} parts, and {result.compatibilities_upserted} compatibility links."
            )

    st.caption("If the website blocks direct fetches, save the listing page as HTML in your browser and import that file below.")

    saved_html_file = st.file_uploader(
        "Saved compatibility listing HTML",
        type=["html", "htm"],
        key="compatibility_saved_html",
    )
    saved_html_text = ""
    saved_html_analysis = None
    if saved_html_file is not None:
        html_bytes = saved_html_file.getvalue()
        try:
            saved_html_text = html_bytes.decode("utf-8")
        except UnicodeDecodeError:
            saved_html_text = html_bytes.decode("latin-1")

        saved_html_analysis = analyze_reference_html(
            saved_html_text,
            reference_url.strip() or saved_html_file.name,
        )

        st.write("Saved HTML analysis:")
        analysis_columns = st.columns(4)
        analysis_columns[0].metric("Page type", saved_html_analysis.page_kind.replace("_", " ").title())
        analysis_columns[1].metric("Detected rows", saved_html_analysis.rows_detected)
        analysis_columns[2].metric("Listing links", len(saved_html_analysis.listing_links))
        analysis_columns[3].metric("Detected models", len(saved_html_analysis.detected_models))
        st.caption(saved_html_analysis.guidance)

        if saved_html_analysis.detected_models:
            st.write("Detected models:")
            st.dataframe(pd.DataFrame({"model": saved_html_analysis.detected_models}), use_container_width=True)

        if saved_html_analysis.listing_links:
            st.write("Discovered listing links:")
            st.dataframe(pd.DataFrame({"listing_url": saved_html_analysis.listing_links}), use_container_width=True)

    if saved_html_file is not None and st.button("Import saved HTML listing", key="import_saved_compatibility_html"):
        try:
            with get_connection() as conn:
                result = import_reference_html(
                    conn,
                    html=saved_html_text,
                    page_url=reference_url.strip() or saved_html_file.name,
                    source_name=reference_source_name.strip() or "HardDrivesDirect",
                )
        except Exception as exc:
            st.error(f"Saved HTML compatibility import failed: {exc}")
        else:
            st.success(
                "Saved HTML import completed. "
                f"Parsed {result.rows_seen} rows, added {result.models_upserted} models, "
                f"{result.parts_upserted} parts, and {result.compatibilities_upserted} compatibility links."
            )

    st.caption("Text-based service manuals or specification PDFs can also be analyzed. This first pass only imports explicit part references found in the PDF text.")

    saved_pdf_file = st.file_uploader(
        "Saved service manual PDF",
        type=["pdf"],
        key="compatibility_saved_pdf",
    )
    saved_pdf_bytes = b""
    saved_pdf_analysis = None
    saved_pdf_preview_rows: list[dict[str, object]] = []
    if saved_pdf_file is not None:
        saved_pdf_bytes = saved_pdf_file.getvalue()
        try:
            saved_pdf_analysis = analyze_reference_pdf(
                saved_pdf_bytes,
                reference_url.strip() or saved_pdf_file.name,
                document_title=saved_pdf_file.name,
            )
            saved_pdf_preview_rows = parse_reference_pdf(
                saved_pdf_bytes,
                reference_url.strip() or saved_pdf_file.name,
                document_title=saved_pdf_file.name,
            )
        except Exception as exc:
            st.error(f"Saved PDF analysis failed: {exc}")
        else:
            st.write("Saved PDF analysis:")
            pdf_columns = st.columns(4)
            pdf_columns[0].metric("Document type", saved_pdf_analysis.page_kind.replace("_", " ").title())
            pdf_columns[1].metric("Pages", saved_pdf_analysis.page_count)
            pdf_columns[2].metric("Detected rows", saved_pdf_analysis.rows_detected)
            pdf_columns[3].metric("Detected models", len(saved_pdf_analysis.detected_models))
            st.caption(saved_pdf_analysis.guidance)

            if saved_pdf_analysis.detected_models:
                st.write("Detected models from PDF:")
                st.dataframe(pd.DataFrame({"model": saved_pdf_analysis.detected_models}), use_container_width=True)

            if saved_pdf_analysis.detected_part_numbers:
                st.write("Detected part numbers from PDF:")
                st.dataframe(pd.DataFrame({"part_number": saved_pdf_analysis.detected_part_numbers}), use_container_width=True)

            if saved_pdf_preview_rows:
                st.write("PDF rows ready to import:")
                preview_df = pd.DataFrame(
                    [
                        {
                            "part_number": row.get("part_number", ""),
                            "description": row.get("description", ""),
                            "models": ", ".join(str(model) for model in row.get("system_models", [])),
                            "page": (row.get("attributes") or {}).get("source_page", ""),
                            "section": (row.get("attributes") or {}).get("document_section", ""),
                        }
                        for row in saved_pdf_preview_rows[:100]
                    ]
                )
                st.dataframe(preview_df, use_container_width=True)
                if len(saved_pdf_preview_rows) > 100:
                    st.caption(f"Showing first 100 of {len(saved_pdf_preview_rows)} parsed PDF rows.")

    if saved_pdf_file is not None and st.button("Import saved PDF manual", key="import_saved_compatibility_pdf"):
        try:
            with get_connection() as conn:
                result = import_reference_pdf(
                    conn,
                    pdf_bytes=saved_pdf_bytes,
                    page_url=reference_url.strip() or saved_pdf_file.name,
                    source_name=reference_source_name.strip() or "PDF Manual",
                    document_title=saved_pdf_file.name,
                )
        except Exception as exc:
            st.error(f"Saved PDF compatibility import failed: {exc}")
        else:
            st.success(
                "Saved PDF import completed. "
                f"Parsed {result.rows_seen} rows, added {result.models_upserted} models, "
                f"{result.parts_upserted} parts, and {result.compatibilities_upserted} compatibility links."
            )

    st.markdown("#### Manual Reference Rows")
    manual_upload = st.file_uploader(
        "Optional CSV or Excel with columns: system_model, part_number, description, manufacturer, aliases",
        type=["csv", "xlsx", "xlsm", "xls"],
        key="compatibility_manual_upload",
    )
    if manual_upload is not None and st.button("Import uploaded reference rows", key="import_uploaded_reference_rows"):
        try:
            if manual_upload.name.lower().endswith(".csv"):
                manual_df = pd.read_csv(manual_upload)
            else:
                manual_df = pd.read_excel(manual_upload)

            required_columns = {"system_model", "part_number", "description"}
            missing_columns = required_columns.difference(manual_df.columns)
            if missing_columns:
                raise ValueError(f"Missing required columns: {', '.join(sorted(missing_columns))}")

            rows_to_import: list[dict[str, object]] = []
            for record in manual_df.fillna("").to_dict("records"):
                aliases = [value.strip() for value in str(record.get("aliases", "")).split(",") if value.strip()]
                rows_to_import.append(
                    {
                        "system_models": [str(record.get("system_model", "")).strip()],
                        "part_number": str(record.get("part_number", "")).strip(),
                        "description": str(record.get("description", "")).strip(),
                        "manufacturer": str(record.get("manufacturer", "")).strip(),
                        "aliases": aliases,
                    }
                )

            with get_connection() as conn:
                result = import_reference_rows(
                    conn,
                    rows=rows_to_import,
                    source_name="Manual Upload",
                    source_type="manual_file",
                    source_url=manual_upload.name,
                )
        except Exception as exc:
            st.error(f"Manual compatibility import failed: {exc}")
        else:
            st.success(
                f"Imported {result.rows_seen} manual row(s), {result.models_upserted} model(s), and {result.compatibilities_upserted} compatibility links."
            )

    st.markdown("#### Unified Compatibility Search")
    compatibility_query = st.text_input(
        "Search by system model or part number",
        placeholder="Examples: DL560 G9, 872737-001, R640",
        key="compatibility_query",
    )
    st.caption("Compatibility = part fits a system model. Alternative/Alias part number = same part family with a different identifier.")
    compatibility_available_only = st.checkbox(
        "Only show related parts that are in stock",
        value=False,
        key="compatibility_available_only_v2",
    )

    if compatibility_query.strip():
        with get_connection() as conn:
            all_compatibility_rows = search_related_compatibility(
                conn,
                compatibility_query.strip(),
                available_only=False,
                limit=300,
            )
            compatibility_rows = search_related_compatibility(
                conn,
                compatibility_query.strip(),
                available_only=compatibility_available_only,
                limit=300,
            )
            hidden_compatibility_rows = []
            if compatibility_available_only and not compatibility_rows:
                hidden_compatibility_rows = all_compatibility_rows[:25]
            model_rows = search_system_models(conn, compatibility_query.strip(), limit=25)
            reference_part_rows = search_reference_parts(conn, compatibility_query.strip(), available_only=False, limit=50)
            inventory_detected_rows = search_inventory_detected_models(conn, compatibility_query.strip(), limit=10)

        metric_columns = st.columns(4)
        metric_columns[0].metric("Related rows", len(all_compatibility_rows))
        metric_columns[1].metric("Imported models", len(model_rows))
        metric_columns[2].metric("Reference parts", len(reference_part_rows))
        metric_columns[3].metric("Inventory models", len(inventory_detected_rows))

        if compatibility_available_only:
            st.caption(f"Showing {len(compatibility_rows)} in-stock compatibility row(s) out of {len(all_compatibility_rows)} total match(es).")

        if compatibility_rows:
            compatibility_df = pd.DataFrame([dict(row) for row in compatibility_rows])
            st.write("Related compatibility results:")
            preferred_columns = [
                "model_name",
                "reference_part_number",
                "alias_part_numbers",
                "reference_description",
                "reference_manufacturer",
                "source_name",
                "qty_on_hand",
                "match_status",
                "relation_type",
            ]
            display_columns = [column for column in preferred_columns if column in compatibility_df.columns]
            st.dataframe(compatibility_df[display_columns], use_container_width=True)
        else:
            st.info("No related compatibility rows matched this search.")
            if hidden_compatibility_rows:
                st.caption(
                    f"{len(hidden_compatibility_rows)} related compatibility row(s) exist, but they are currently hidden by the in-stock filter."
                )
                with st.expander("Compatibility rows hidden by stock filter", expanded=False):
                    st.dataframe(pd.DataFrame([dict(row) for row in hidden_compatibility_rows]), use_container_width=True)

        with st.expander("Matching imported system models", expanded=False):
            if model_rows:
                st.dataframe(pd.DataFrame([dict(row) for row in model_rows]), use_container_width=True)
            else:
                st.info("No imported system models matched this search.")

        with st.expander("Matching imported reference parts", expanded=False):
            if reference_part_rows:
                reference_df = pd.DataFrame([dict(row) for row in reference_part_rows])
                preferred_columns = [
                    "reference_part_number",
                    "alias_part_numbers",
                    "reference_description",
                    "reference_manufacturer",
                    "compatible_model_count",
                    "compatible_models",
                    "local_part_number",
                    "qty_on_hand",
                    "match_status",
                ]
                display_columns = [column for column in preferred_columns if column in reference_df.columns]
                st.dataframe(reference_df[display_columns], use_container_width=True)
            else:
                st.info("No imported reference parts matched this search.")

        with st.expander("Inventory-detected models", expanded=False):
            if inventory_detected_rows:
                inventory_detected_df = pd.DataFrame(
                    [
                        {
                            "brand": row["brand"],
                            "model": row["model"],
                            "family": row["family"],
                            "inventory_mentions": row["inventory_mentions"],
                            "distinct_parts": row["distinct_parts"],
                            "priority_score": row["priority_score"],
                            "example_part": row["examples"][0]["part_number"] if row["examples"] else "",
                            "example_description": row["examples"][0]["description"] if row["examples"] else "",
                        }
                        for row in inventory_detected_rows
                    ]
                )
                st.dataframe(inventory_detected_df, use_container_width=True)
            else:
                st.info("No HP/HPE or Dell model mentions were detected in current inventory descriptions for this search.")
    else:
        st.info("Search by part number or system model to see all related imported models, reference parts, and inventory matches in one place.")

with adjustment_tab:
    st.subheader("Stock Use and Adjustment")
    st.write("Choose an inventory row, then record stock usage or a positive adjustment. Negative stock is blocked.")

    adjustment_query = st.text_input(
        "Find inventory row",
        placeholder="Search by part number or description",
    )
    adjustment_available_only = st.checkbox(
        "Only show rows with stock on hand",
        value=True,
        key="adjustment_available_only",
    )

    with get_connection() as conn:
        adjustment_rows = search_inventory_records(
            conn,
            query=adjustment_query,
            available_only=adjustment_available_only,
            limit=200,
        )

    if adjustment_rows:
        adjustment_options = [dict(row) for row in adjustment_rows]

        def format_adjustment_option(option: dict[str, object]) -> str:
            return (
                f"{option['part_number']} | {option['warehouse_code']} / {option['location_code']} | "
                f"Qty {float(option['qty_on_hand']):.2f} | {option['oem']} | {option['description']}"
            )

        selected_option = st.selectbox(
            "Inventory row",
            options=adjustment_options,
            format_func=format_adjustment_option,
        )

        st.dataframe(
            pd.DataFrame(adjustment_options)[
                ["part_number", "description", "oem", "warehouse_code", "location_code", "qty_on_hand", "uom", "updated_at"]
            ].rename(columns={"oem": "OEM"}),
            use_container_width=True,
        )

        with st.form("inventory_adjustment_form"):
            action = st.radio(
                "Action",
                options=["Use stock", "Add stock"],
                horizontal=True,
                key="adjustment_action",
            )
            quantity = st.number_input(
                "Quantity",
                min_value=0.01,
                step=1.0,
                key="adjustment_quantity",
            )
            adjustment_reference = st.text_input(
                "Adjustment reference",
                key="adjustment_reference",
            )
            adjustment_notes = st.text_area(
                "Notes",
                placeholder="Reason for the adjustment",
                key="adjustment_notes",
            )
            submitted = st.form_submit_button("Record adjustment", type="primary")

        if submitted:
            qty_change = -quantity if action == "Use stock" else quantity
            try:
                with get_connection() as conn:
                    record_inventory_adjustment(
                        conn=conn,
                        part_id=int(selected_option["part_id"]),
                        location_id=int(selected_option["location_id"]),
                        qty_change=float(qty_change),
                        created_by=operator_name.strip() or "system",
                        reference=adjustment_reference.strip() or "manual-adjustment",
                        notes=adjustment_notes.strip(),
                    )
            except ValueError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"Adjustment failed: {exc}")
            else:
                st.session_state["reset_adjustment_form"] = True
                st.success("Inventory adjustment recorded.")
                st.rerun()
    else:
        st.info("No inventory rows are available for adjustment with the current filters.")