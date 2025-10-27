import re
from typing import Optional

import pandas as pd
import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

dataframe_store = {"df": None}


MONTH_MAP = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


QUARTER_MAP = {
    "q1": (1, 3),
    "q2": (4, 6),
    "q3": (7, 9),
    "q4": (10, 12),
}


def load_and_clean_file(file_storage, filename: str) -> pd.DataFrame:
    if filename.lower().endswith('.xlsx') or filename.lower().endswith('.xls'):
        df = pd.read_excel(file_storage)
    else:
        df = pd.read_csv(file_storage)
    
    df.columns = [col.strip() for col in df.columns]

    for column in df.columns:
        if pd.api.types.is_string_dtype(df[column]):
            numeric_candidate = pd.to_numeric(
                df[column]
                .astype(str)
                .str.replace(",", "", regex=False)
                .str.strip(),
                errors="coerce",
            )
            if numeric_candidate.notna().sum() > 0:
                df[column] = numeric_candidate
                continue

            datetime_candidate = pd.to_datetime(
                df[column], errors="coerce"
            )
            if datetime_candidate.notna().sum() > 0:
                df[column] = datetime_candidate
            else:
                df[column] = df[column].fillna("").astype(str).str.strip()

    numeric_cols = df.select_dtypes(include=["number"]).columns
    df[numeric_cols] = df[numeric_cols].fillna(0)

    object_cols = df.select_dtypes(include=["object", "string"]).columns
    df[object_cols] = df[object_cols].fillna("")

    return df


def _find_date_column(df: pd.DataFrame) -> Optional[str]:
    for column in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[column]):
            return column
    for column in df.columns:
        if "date" in column.lower() or "month" in column.lower():
            candidate = pd.to_datetime(df[column], errors="coerce")
            if candidate.notna().sum() > 0:
                df[column] = candidate
                return column
    return None


def _parse_top_n(question: str) -> int:
    match = re.search(r"top\s+(\d+)", question)
    if match:
        return int(match.group(1))
    return 5


def basic_pandas_insights(df: pd.DataFrame, question: str) -> str:
    question_lower = question.lower()
    insights = []

    date_column = _find_date_column(df)
    sales_column = None
    for column in df.columns:
        if "sale" in column.lower() and pd.api.types.is_numeric_dtype(df[column]):
            sales_column = column
            break

    if "total" in question_lower and sales_column:
        target_month = None
        for month_name, month_number in MONTH_MAP.items():
            if month_name in question_lower:
                target_month = month_number
                break
        if target_month and date_column:
            month_mask = df[date_column].dt.month == target_month
            total_sales = df.loc[month_mask, sales_column].sum()
            insights.append(
                f"Computed total {sales_column} for month {target_month}: {total_sales:.2f}"
            )

        if "quarter" in question_lower or any(q in question_lower for q in QUARTER_MAP):
            for label, (start_month, end_month) in QUARTER_MAP.items():
                if label in question_lower and date_column:
                    quarter_mask = df[date_column].dt.month.between(start_month, end_month)
                    quarter_total = df.loc[quarter_mask, sales_column].sum()
                    insights.append(
                        f"Total {sales_column} for {label.upper()} is {quarter_total:.2f}"
                    )

    if "compare" in question_lower and date_column and sales_column:
        comparisons = []
        for label, (start_month, end_month) in QUARTER_MAP.items():
            if label in question_lower:
                mask = df[date_column].dt.month.between(start_month, end_month)
                comparisons.append((label.upper(), df.loc[mask, sales_column].sum()))
        if len(comparisons) >= 2:
            formatted = "; ".join(f"{label}: {total:.2f}" for label, total in comparisons)
            insights.append(f"Quarter comparison totals - {formatted}")

    if "top" in question_lower and ("product" in question_lower or "best" in question_lower):
        product_column = None
        for column in df.columns:
            if "product" in column.lower() and df[column].dtype == object:
                product_column = column
                break
        if product_column and sales_column:
            top_n = _parse_top_n(question_lower)
            grouped = (
                df.groupby(product_column)[sales_column]
                .sum()
                .sort_values(ascending=False)
                .head(top_n)
            )
            insights.append(
                "Top products by sales:\n" + grouped.to_string()
            )

    if "summary" in question_lower and sales_column:
        category_column = None
        for column in df.columns:
            if "category" in column.lower():
                category_column = column
                break
        if category_column:
            summary = df.groupby(category_column)[sales_column].sum().sort_values(ascending=False)
            insights.append("Sales summary by category:\n" + summary.to_string())

    return "\n\n".join(insights)


def answer_question(question: str) -> str:
    df = dataframe_store.get("df")
    if df is None:
        return "Please upload a CSV first."

    columns_info = "\n".join(f"- {col} ({dtype})" for col, dtype in zip(df.columns, df.dtypes))
    full_data = df.to_string(index=False)
    describe_summary = df.describe(include="all").to_string()
    heuristics_output = basic_pandas_insights(df, question)

    prompt_parts = [
        "You are a data analyst helping a business user understand their sales data.",
        f"User question: {question}",
        "Dataset column information:",
        columns_info,
        "Full dataset:",
        full_data,
        "Descriptive statistics:",
        describe_summary,
    ]

    if heuristics_output:
        prompt_parts.append("Direct pandas insights:\n" + heuristics_output)

    prompt_parts.append(
        "Use the provided context to answer clearly and concisely. Highlight key numbers and comparisons in plain language."
    )

    prompt = "\n\n".join(prompt_parts)
    return call_llm(prompt)


def call_llm(prompt: str) -> str:
    ollama_url = "http://localhost:11434/api/generate"
    
    try:
        response = requests.post(
            ollama_url,
            json={
                "model": "llama3.2",
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.2,
                    "num_predict": 300
                }
            },
            timeout=60
        )
        
        if response.status_code == 200:
            result = response.json()
            return result.get("response", "No response from model").strip()
        else:
            return f"Error from Ollama: {response.status_code} - {response.text}"
            
    except requests.exceptions.ConnectionError:
        return "Ollama is not running. Please start Ollama and pull the llama3.2 model."
    except Exception as exc:
        return f"Error calling Ollama: {exc}"


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload_csv():
    if "file" not in request.files:
        return jsonify({"error": "No file part in the request."}), 400

    file = request.files["file"]
    
    if file.filename == "":
        return jsonify({"error": "No file selected."}), 400

    allowed_extensions = ('.csv', '.xlsx', '.xls')
    if not file.filename.lower().endswith(allowed_extensions):
        return jsonify({"error": f"Please upload a CSV or Excel file ({', '.join(allowed_extensions)})."}), 400

    try:
        df = load_and_clean_file(file, file.filename)
    except Exception as exc:
        return jsonify({"error": f"Failed to process file: {exc}"}), 400

    dataframe_store["df"] = df
    return jsonify({"success": True, "columns": df.columns.tolist()})


@app.route("/ask", methods=["POST"])
def ask_question():
    data = request.get_json(silent=True) or {}
    question = data.get("question", "").strip()
    if not question:
        return jsonify({"error": "Question is required."}), 400

    df = dataframe_store.get("df")
    if df is None:
        return jsonify({"error": "Please upload a CSV first."}), 400

    answer = answer_question(question)
    return jsonify({"answer": answer})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
