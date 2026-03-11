from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from modules.db import get_connection, initialize_database, record_inventory_adjustment
from modules.import_excel import analyze_snapshot_import, DEFAULT_COLUMN_MAP, import_snapshot, read_excel_preview, recent_import_runs, summarize_import
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

import_tab, search_tab, adjustment_tab = st.tabs(["Import", "Inventory Search", "Stock Adjustment"])

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
    query = st.text_input("Search by part number or description")
    available_only = st.checkbox("Available stock only", value=True)

    with get_connection() as conn:
        rows = search_inventory(conn, query=query, available_only=available_only, limit=200)
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