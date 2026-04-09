from datetime import datetime
from io import StringIO
from pathlib import Path
from uuid import uuid4

import pandas as pd
import plotly.express as px
import streamlit as st

from agent import SQLDeveloperCrew
from dataset_store import (
    dataframe_from_csv_bytes,
    dataframe_from_json_text,
    extract_table_from_image,
    get_dataset,
    import_csv_dataset,
    import_dataframe_dataset,
    import_json_dataset,
    import_multi_json_dataset,
    list_datasets,
    load_dataset_previews,
    load_sample_json_text,
    load_sql_result,
)
from db_tools import get_last_query_context


APP_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap');

html, body, [clatoss*="css"] {
  font-family: "Inter", sans-serif;
}

[data-testid="stChatMessage"] {
  border-radius: 16px;
  padding: 0.8rem 1rem;
}
</style>
"""


def format_app_error(error: Exception) -> str:
    message = str(error)
    lowered = message.lower()
    if (
        "ratelimiterror" in lowered
        or "resource_exhausted" in lowered
        or '"code":429' in lowered
        or "quota exceeded" in lowered
    ):
        return (
            "Ban da vuot gioi han quota Gemini free tier. "
            "Doi khoang 1 phut roi thu lai, hoac doi API key/model khac."
        )
    return message


def now_text() -> str:
    return datetime.now().strftime("%H:%M")


def serialize_dataframe(dataframe: pd.DataFrame | None) -> str | None:
    if dataframe is None or dataframe.empty:
        return None
    return dataframe.to_json(orient="split", force_ascii=False)


def deserialize_dataframe(payload: str | None) -> pd.DataFrame | None:
    if not payload:
        return None
    return pd.read_json(StringIO(payload), orient="split")


def init_state(dataset_options: list[dict]) -> None:
    dataset_ids = [dataset["dataset_id"] for dataset in dataset_options]
    default_dataset_id = dataset_ids[0] if dataset_ids else None

    if "chat_sessions" not in st.session_state:
        st.session_state.chat_sessions = {}
    if "active_chat_id" not in st.session_state:
        st.session_state.active_chat_id = None
    if "pending_prompt" not in st.session_state:
        st.session_state.pending_prompt = None

    if not st.session_state.chat_sessions and default_dataset_id is not None:
        chat_id = create_chat_session(default_dataset_id)
        st.session_state.active_chat_id = chat_id

    active_chat = get_active_chat()
    if active_chat is None and st.session_state.chat_sessions:
        first_chat_id = next(iter(st.session_state.chat_sessions))
        st.session_state.active_chat_id = first_chat_id
        active_chat = get_active_chat()

    if active_chat and active_chat["dataset_id"] not in dataset_ids and default_dataset_id is not None:
        active_chat["dataset_id"] = default_dataset_id


def create_chat_session(dataset_id: str, title: str = "New analysis") -> str:
    chat_id = str(uuid4())
    st.session_state.chat_sessions[chat_id] = {
        "id": chat_id,
        "title": title,
        "dataset_id": dataset_id,
        "messages": [],
        "created_at": datetime.now().isoformat(),
    }
    return chat_id


def get_active_chat() -> dict | None:
    chat_id = st.session_state.get("active_chat_id")
    if chat_id is None:
        return None
    return st.session_state.chat_sessions.get(chat_id)


def set_active_chat(chat_id: str) -> None:
    st.session_state.active_chat_id = chat_id


def update_active_chat_dataset(dataset_id: str) -> None:
    active_chat = get_active_chat()
    if active_chat is not None:
        active_chat["dataset_id"] = dataset_id


def update_chat_title_from_prompt(prompt: str) -> None:
    active_chat = get_active_chat()
    if active_chat is None:
        return
    if active_chat["title"] != "New analysis":
        return
    compact = " ".join(prompt.strip().split())
    active_chat["title"] = compact[:42] + ("..." if len(compact) > 42 else "")


def render_sidebar(datasets: list[dict], active_dataset_id: str | None) -> None:
    dataset_map = {dataset["dataset_id"]: dataset for dataset in datasets}

    with st.sidebar:
        st.image("../img/hyper.png", width=120)
        st.markdown("### Hyperlogy Analyst")

        if st.button("+ New chat", use_container_width=True, type="primary"):
            if active_dataset_id is None and datasets:
                active_dataset_id = datasets[0]["dataset_id"]
            if active_dataset_id is not None:
                chat_id = create_chat_session(active_dataset_id)
                set_active_chat(chat_id)
                st.rerun()

        st.divider()

        for chat_id, chat in st.session_state.chat_sessions.items():
            is_active = chat_id == st.session_state.active_chat_id
            button_type = "primary" if is_active else "tertiary"
            if st.button(chat["title"], key=f"chat-{chat_id}", use_container_width=True, type=button_type):
                set_active_chat(chat_id)
                st.rerun()

        st.divider()

        # Dataset selector
        if datasets:
            selected_dataset_id = st.selectbox(
                "Dataset",
                options=[dataset["dataset_id"] for dataset in datasets],
                index=[dataset["dataset_id"] for dataset in datasets].index(active_dataset_id)
                if active_dataset_id in [dataset["dataset_id"] for dataset in datasets]
                else 0,
                format_func=lambda did: dataset_map[did]["display_name"],
            )
            if selected_dataset_id != active_dataset_id:
                update_active_chat_dataset(selected_dataset_id)
                st.rerun()

            # Dataset preview
            ds = dataset_map.get(active_dataset_id)
            if ds:
                table_names = ds.get("table_names", [])
                st.caption(f"{ds['row_count']} rows / {ds['table_count']} tables")
                for tn in table_names:
                    st.markdown(f"- `{tn}`")
                with st.expander("Preview tables"):
                    previews = load_dataset_previews(ds["dataset_id"], limit=5)
                    for table_name, preview in previews.items():
                        st.caption(table_name)
                        st.dataframe(preview, use_container_width=True, hide_index=True, height=150)
        else:
            st.caption("Chua co dataset — import du lieu ben duoi.")

        # Import section
        with st.expander("Import dataset"):
            import_type = st.radio("Type", ["CSV", "JSON", "Image", "Sample JSON"], horizontal=True, label_visibility="collapsed")

            if import_type == "CSV":
                uploaded_csv = st.file_uploader("Upload CSV", type=["csv"], key="csv-uploader", label_visibility="collapsed")
                if uploaded_csv is not None:
                    file_bytes = uploaded_csv.getvalue()
                    try:
                        preview = dataframe_from_csv_bytes(file_bytes)
                        st.dataframe(preview.head(5), use_container_width=True, hide_index=True, height=150)
                        dataset_name = st.text_input(
                            "Name",
                            value=Path(uploaded_csv.name).stem.replace("_", " ").title(),
                            key="csv-dataset-name",
                        )
                        if st.button("Import", key="import-csv", use_container_width=True):
                            dataset = import_csv_dataset(
                                display_name=dataset_name,
                                file_bytes=file_bytes,
                                source_file=uploaded_csv.name,
                            )
                            update_active_chat_dataset(dataset["dataset_id"])
                            st.rerun()
                    except Exception as error:
                        st.error(format_app_error(error))

            elif import_type == "JSON":
                uploaded_jsons = st.file_uploader(
                    "Upload JSON (co the chon nhieu file)",
                    type=["json"],
                    key="json-uploader",
                    label_visibility="collapsed",
                    accept_multiple_files=True,
                )
                if uploaded_jsons:
                    try:
                        file_infos: list[dict[str, str]] = []
                        file_summaries: list[str] = []
                        for uf in uploaded_jsons:
                            raw = uf.getvalue().decode("utf-8")
                            file_infos.append({"name": uf.name, "text": raw})
                            preview = dataframe_from_json_text(raw)
                            file_summaries.append(f"{Path(uf.name).stem} ({len(preview)} rows)")

                        with st.expander(f"Preview: {', '.join(file_summaries)}", expanded=False):
                            for fi in file_infos:
                                prev = dataframe_from_json_text(fi["text"])
                                st.caption(Path(fi["name"]).stem)
                                st.dataframe(prev.head(3), use_container_width=True, hide_index=True, height=120)

                        default_name = (
                            Path(uploaded_jsons[0].name).stem.replace("_", " ").title()
                            if len(uploaded_jsons) == 1
                            else "Multi JSON Dataset"
                        )
                        dataset_name = st.text_input("Name", value=default_name, key="json-dataset-name")

                        if st.button("Import", key="import-json", use_container_width=True):
                            if len(file_infos) == 1:
                                dataset = import_json_dataset(
                                    display_name=dataset_name,
                                    raw_text=file_infos[0]["text"],
                                    source_file=file_infos[0]["name"],
                                )
                            else:
                                dataset = import_multi_json_dataset(
                                    display_name=dataset_name,
                                    files=file_infos,
                                )
                            update_active_chat_dataset(dataset["dataset_id"])
                            st.rerun()
                    except Exception as error:
                        st.error(format_app_error(error))

            elif import_type == "Image":
                uploaded_img = st.file_uploader(
                    "Upload image", type=["png", "jpg", "jpeg", "webp"], key="img-uploader", label_visibility="collapsed"
                )
                if uploaded_img is not None:
                    image_bytes = uploaded_img.getvalue()
                    st.image(image_bytes, use_container_width=True)
                    mime_map = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}
                    ext = Path(uploaded_img.name).suffix.lstrip(".").lower()
                    mime_type = mime_map.get(ext, "image/png")
                    try:
                        # Cache OCR result to avoid re-running on Import click
                        cache_key = f"ocr_cache_{uploaded_img.name}_{len(image_bytes)}"
                        if cache_key not in st.session_state:
                            with st.spinner("Dang OCR bang tu anh..."):
                                st.session_state[cache_key] = extract_table_from_image(image_bytes, mime_type)
                        preview = st.session_state[cache_key]

                        st.dataframe(preview.head(5), use_container_width=True, hide_index=True, height=150)
                        dataset_name = st.text_input(
                            "Name",
                            value=Path(uploaded_img.name).stem.replace("_", " ").title(),
                            key="img-dataset-name",
                        )
                        if st.button("Import", key="import-img", use_container_width=True):
                            dataframe = st.session_state.pop(cache_key)
                            description = "Dataset duoc import tu anh bang qua EasyOCR."
                            dataset = import_dataframe_dataset(
                                dataframe=dataframe,
                                display_name=dataset_name,
                                source_type="image",
                                source_file=uploaded_img.name,
                                description=description,
                            )
                            update_active_chat_dataset(dataset["dataset_id"])
                            st.rerun()
                    except Exception as error:
                        st.error(format_app_error(error))

            else:
                sample_name = st.text_input("Name", value="JSON Sales Demo", key="sample-name")
                if st.button("Load sample", key="import-sample", use_container_width=True):
                    dataset = import_json_dataset(
                        display_name=sample_name,
                        raw_text=load_sample_json_text(),
                        source_file="data/samples/sales_demo.json",
                    )
                    update_active_chat_dataset(dataset["dataset_id"])
                    st.rerun()


def _detect_date_column(dataframe: pd.DataFrame) -> str | None:
    """Try to find a date/time column by name or content."""
    date_keywords = ("date", "time", "ngay", "thang", "nam", "month", "year", "day", "created", "updated")
    for col in dataframe.columns:
        if any(kw in col.lower() for kw in date_keywords):
            return col
    for col in dataframe.select_dtypes(include=["object", "datetime"]).columns:
        sample = dataframe[col].dropna().head(20)
        if sample.empty:
            continue
        try:
            pd.to_datetime(sample)
            return col
        except (ValueError, TypeError):
            continue
    return None


def render_chart_for_dataframe(dataframe: pd.DataFrame | None) -> None:
    if dataframe is None or dataframe.empty:
        st.info("Khong co du lieu de ve chart.")
        return

    numeric_cols = list(dataframe.select_dtypes(include="number").columns)
    category_cols = [c for c in dataframe.columns if c not in numeric_cols]
    date_col = _detect_date_column(dataframe)

    if not numeric_cols:
        # No numeric columns — try counting by category (e.g. COUNT(*) GROUP BY type)
        if category_cols:
            col = category_cols[0]
            counts = dataframe[col].value_counts()
            if len(counts) <= 12:
                fig = px.pie(names=counts.index, values=counts.values, title=f"Phan bo theo {col}")
            else:
                fig = px.bar(x=counts.index[:20], y=counts.values[:20], labels={"x": col, "y": "count"}, title=f"Top 20 {col}")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Ket qua khong co cot so de ve chart.")
        return

    primary_num = numeric_cols[0]

    # --- TIME SERIES: date column detected ---
    if date_col:
        df_sorted = dataframe.copy()
        try:
            df_sorted[date_col] = pd.to_datetime(df_sorted[date_col])
            df_sorted = df_sorted.sort_values(date_col)
        except (ValueError, TypeError):
            pass
        fig = px.line(df_sorted, x=date_col, y=numeric_cols[:4], title=f"Xu huong theo {date_col}")
        st.plotly_chart(fig, use_container_width=True)
        return

    # --- PIE CHART: 1 category + 1 numeric, few rows ---
    if len(category_cols) >= 1 and len(dataframe) <= 12 and len(numeric_cols) == 1:
        fig = px.pie(dataframe, names=category_cols[0], values=primary_num, title=f"{primary_num} theo {category_cols[0]}")
        st.plotly_chart(fig, use_container_width=True)
        return

    # --- GROUPED BAR: 1 category + multiple numeric ---
    if len(category_cols) >= 1 and len(numeric_cols) >= 2:
        cat = category_cols[0]
        df_melted = dataframe.melt(id_vars=[cat], value_vars=numeric_cols[:5], var_name="metric", value_name="value")
        fig = px.bar(df_melted, x=cat, y="value", color="metric", barmode="group", title=f"So sanh cac chi so theo {cat}")
        st.plotly_chart(fig, use_container_width=True)
        return

    # --- HORIZONTAL BAR: 1 category + 1 numeric, many rows ---
    if len(category_cols) >= 1 and len(dataframe) > 12:
        cat = category_cols[0]
        df_top = dataframe.nlargest(20, primary_num)
        fig = px.bar(df_top, y=cat, x=primary_num, orientation="h", title=f"Top 20 {cat} theo {primary_num}")
        fig.update_layout(yaxis={"categoryorder": "total ascending"})
        st.plotly_chart(fig, use_container_width=True)
        return

    # --- BAR CHART: 1 category + 1 numeric ---
    if len(category_cols) >= 1:
        cat = category_cols[0]
        fig = px.bar(dataframe, x=cat, y=primary_num, title=f"{primary_num} theo {cat}")
        st.plotly_chart(fig, use_container_width=True)
        return

    # --- SCATTER: 2+ numeric, no category ---
    if len(numeric_cols) >= 2:
        fig = px.scatter(dataframe, x=numeric_cols[0], y=numeric_cols[1], title=f"{numeric_cols[1]} vs {numeric_cols[0]}")
        st.plotly_chart(fig, use_container_width=True)
        return

    # --- HISTOGRAM: single numeric column ---
    fig = px.histogram(dataframe, x=primary_num, title=f"Phan bo {primary_num}")
    st.plotly_chart(fig, use_container_width=True)


def render_assistant_panels(message: dict) -> None:
    sql_text = message.get("sql")
    result_df = deserialize_dataframe(message.get("result_table_json"))

    if not sql_text and result_df is None:
        st.markdown(message["content"])
        return

    st.markdown(message["content"])

    tabs = st.tabs(["SQL", "Table", "Chart"])
    with tabs[0]:
        if sql_text:
            st.code(sql_text, language="sql")
        else:
            st.info("Khong co SQL cho tin nhan nay.")
    with tabs[1]:
        if result_df is not None:
            st.dataframe(result_df, use_container_width=True, hide_index=True)
        else:
            st.info("Khong co bang ket qua.")
    with tabs[2]:
        render_chart_for_dataframe(result_df)


def render_chat_messages(messages: list[dict]) -> None:
    for message in messages:
        with st.chat_message(message["role"]):
            if message["role"] == "assistant":
                render_assistant_panels(message)
            else:
                st.markdown(message["content"])


def build_assistant_message(query: str, dataset: dict) -> dict:
    crew_instance = SQLDeveloperCrew(dataset_context=dataset)
    response = crew_instance.run_query(query)
    query_context = get_last_query_context()
    result_frame = None

    if query_context["sql"]:
        try:
            result_frame = load_sql_result(query_context["sql"])
        except Exception:
            result_frame = None

    return {
        "role": "assistant",
        "content": str(response),
        "sql": query_context["sql"],
        "result_table_json": serialize_dataframe(result_frame),
        "created_at": now_text(),
    }


def process_prompt(prompt: str, dataset: dict | None) -> None:
    active_chat = get_active_chat()
    if active_chat is None:
        return
    if dataset is None:
        st.error("Chua co dataset active de phan tich.")
        return

    update_chat_title_from_prompt(prompt)
    user_message = {"role": "user", "content": prompt, "created_at": now_text()}
    active_chat["messages"].append(user_message)

    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Dang phan tich..."):
            try:
                assistant_message = build_assistant_message(prompt, dataset)
                active_chat["messages"].append(assistant_message)
                render_assistant_panels(assistant_message)
            except Exception as error:
                error_message = {"role": "assistant", "content": f"Loi: {format_app_error(error)}"}
                active_chat["messages"].append(error_message)
                st.error(error_message["content"])


def main() -> None:
    st.set_page_config(page_title="Hyperlogy Analyst", page_icon="💬", layout="centered")
    st.markdown(APP_CSS, unsafe_allow_html=True)

    datasets = list_datasets()
    init_state(datasets)
    active_chat = get_active_chat()
    active_dataset_id = active_chat["dataset_id"] if active_chat else None
    active_dataset = get_dataset(active_dataset_id) if active_dataset_id else None

    render_sidebar(datasets, active_dataset_id)

    # Chat area - full width, clean
    if active_chat is not None and active_chat["messages"]:
        render_chat_messages(active_chat["messages"])
    else:
        st.markdown("#### What would you like to analyze?")
        suggestions = [
            "Top 5 san pham ban chay nhat?",
            "Tom tat doanh thu theo thang",
            "Phan tich phuong thuc thanh toan",
        ]
        cols = st.columns(len(suggestions))
        for col, prompt in zip(cols, suggestions):
            if col.button(prompt, use_container_width=True):
                st.session_state.pending_prompt = prompt

    prompt = st.chat_input(
        f"Ask about {active_dataset['display_name'] if active_dataset else 'your dataset'}..."
    )
    queued_prompt = st.session_state.pop("pending_prompt", None)
    final_prompt = prompt or queued_prompt
    if final_prompt:
        process_prompt(final_prompt, active_dataset)


if __name__ == "__main__":
    main()
