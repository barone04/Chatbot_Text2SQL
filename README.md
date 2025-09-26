# Hyperlogy

![Giao diện Chatbot](img/hyper.png)

---


## 🤖 Chatbot SVM

Chatbot SVM is a chatbot application that integrates Streamlit, Gemini model and Crewai agent to answer questions from a SQLite3 database that was originally built from CSV files containing product and invoice data.

The chatbot understands natural language questions, translates them into SQL queries, and retrieves answers from the database.

Additionally, it supports internal tools to view table schemas, list available tables, validate SQL statements, and execute queries directly. Vectorstore-based document retrieval is also integrated for enhanced question answering.

---

## 📁 Project Structure

```
Chatbot_SVM/
├── data/
│   ├── data/                   # Python scripts to load/upload CSV to SQLite
│   │   ├── __init__.py
│   │   ├── initial_load.py     # Load CSVs into SQLite
│   │   └── upload_data.py
│
├── img/
│   └── hyper.png               # UI image/logo
│
├── src/
│   ├── chat_log/               # Logs & prompts
│   │   ├── chat_log.txt
│   │   ├── crew.log.txt
│   │   └── prompts.jsonl
│   │
│   ├── config/                 # CrewAI configuration
│   │   ├── agents.yaml
│   │   └── tasks.yaml
│   │
│   ├── __init__.py
│   ├── agent.py                # Agent initialization
│   ├── db_tools.py             # Custom BaseTool classes for SQL tools
│   └── main.py                 # Main Streamlit chatbot script
│
├── .env                        # API keys and secrets (e.g. GOOGLE_API_KEY)
├── environment.yml             # Conda environment file
└── README.md                   # Documentation and usage guide
```

---

## 🚀 Installation Guide

### 1. Clone repository

```bash
git clone https://github.com/barone04/Chatbot_SVM.git
cd Chatbot_SVM
```

### 2. Create environment from `environment.yml`

```bash
conda env create -f environment.yml
conda activate Chatbot_SVM
```

> 💡 If you’ve installed additional libraries, use `conda env update -f environment.yml` to sync the environment.

### 3. Set up environment variables

Create a `.env` file in either `Chatbot_SVM/` or inside `src/` (where `agent.py` resides) and add:
```
GOOGLE_API_KEY=your_gemini_api_key
```

> 🔑 You can get your API key here: https://aistudio.google.com/app/apikey

### 4. Run the app

```bash
cd src
streamlit run agent.py
```

---

## 🧠 Key Features

| Feature | Description |
|----------|-------|
| 💬 Chatbot | Ask a questions and convert to SQL query to access in SQL database |
| 🛠️ Tools | Includes `list_tables`, `tables_schema`, `execute_sql`, `check_sql` |
| 🧠 Agents | Modular and role-based agents, each responsible for a different task |
| 📋 Tasks | Defined workflows such as transforming natural language questions into SQL, validating them, executing, and summarizing results. |

---

## ❗ Notes

- The Gemini model is limited to **200 requests/day for free-tier users**.
- If you hit a `RateLimitError`, you can:
  - Wait until the next day
  - Switch to a different API Key
  - Upgrade your Google AI plan

---

## 💡 Tips

- When using app, you can ask it like:
  ```
  - Cho tôi biết số lượng sản phẩm chocopie được bán ra vào tháng 5/2025?
  - Vẽ bảng thống kê phương thức thanh toán và thời gian bán ra.
  ```

---

## 📬 Contact

Feel free to send ideas, bug reports, or contributions:

**Author:** [@barone04](https://github.com/barone04)  
**Email:** *tbao041024@gmail.com*
