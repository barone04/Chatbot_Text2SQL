import yaml
from crewai import Agent, Task, Crew, Process
from db_tools import clear_query_state, load_llm, set_active_tables
from crewai.project import CrewBase, agent, crew, task
from db_tools import ListTablesTool, TablesSchemaTool, ExecuteSQLTool


#=============== LOAD TOOLS ==============
list_table = ListTablesTool()
table_schema = TablesSchemaTool()
execute_sql = ExecuteSQLTool()


@CrewBase
class SQLDeveloperCrew():
    """SQLDeveloperCrew"""
    def __init__(self, dataset_context=None):
        with open("config/agents.yaml", "r", encoding="utf-8") as f:
            self.agents_config = yaml.safe_load(f)
        with open("config/tasks.yaml", "r", encoding="utf-8") as f:
            self.tasks_config = yaml.safe_load(f)

        self.llm = load_llm()
        self.dataset_context = dataset_context or {}
        set_active_tables(self.dataset_context.get("table_names"))

#============== AGENTS =====================
    @agent
    def sql_dev(self) -> Agent:
        cfg = self.agents_config['sql_dev']
        return Agent(
            role=cfg["role"],
            goal=cfg["goal"],
            backstory=cfg["backstory"],
            llm=self.llm,
            tools=[list_table, table_schema, execute_sql],
            allow_delegation=False,
        )

    @agent
    def data_analyst(self) -> Agent:
        cfg = self.agents_config['data_analyst']
        return Agent(
            role=cfg["role"],
            goal=cfg["goal"],
            backstory=cfg["backstory"],
            llm=self.llm,
            allow_delegation=False,
        )

    @agent
    def report_writer(self) -> Agent:
        cfg = self.agents_config['report_writer']
        return Agent(
            role=cfg["role"],
            goal=cfg["goal"],
            backstory=cfg["backstory"],
            llm=self.llm,
            allow_delegation=False,
        )

#==================== TASKS ======================
    @task
    def extract_data(self) -> Task:
        task_cfg = self.tasks_config["extract_data"]
        return Task(
            description=task_cfg["description"],
            expected_output=task_cfg["expected_output"],
            agent=task_cfg["agent"],
        )

    @task
    def analyze_data(self) -> Task:
        task_cfg = self.tasks_config["analyze_data"]
        return Task(
            description=task_cfg["description"],
            expected_output=task_cfg["expected_output"],
            agent=task_cfg["agent"],
            context=task_cfg["context"],
        )

    @task
    def write_report(self) -> Task:
        task_cfg = self.tasks_config["write_report"]
        return Task(
            description=task_cfg["description"],
            expected_output=task_cfg["expected_output"],
            agent=task_cfg["agent"],
            context=task_cfg["context"],
        )

#==================== CREW ==========================
    @crew
    def crew(self) -> Crew:
        """Creates the SQLDeveloperCrew"""
        return Crew(
            agents=self.agents,
            tasks=[self.extract_data(), self.analyze_data()],
            process=Process.sequential,
            verbose=True,
            memory=False,
            output_log_file="crew.log",
        )

    def build_inputs(self, query: str) -> dict:
        table_names = self.dataset_context.get("table_names", [])
        return {
            "query": query,
            "dataset_name": self.dataset_context.get("display_name", "Active dataset"),
            "dataset_description": self.dataset_context.get(
                "description", "Khong co mo ta bo sung."
            ),
            "allowed_tables": ", ".join(table_names),
            "schema_summary": self.dataset_context.get(
                "schema_summary", "Khong co schema summary."
            ),
        }

    def run_query(self, query: str):
        clear_query_state()
        set_active_tables(self.dataset_context.get("table_names"))
        return self.crew().kickoff(inputs=self.build_inputs(query))


# #======================= TEST ==========================
# print("Nhập câu hỏi:")
# query = sys.stdin.buffer.readline().decode("utf-8", errors="ignore").strip()
#
# inputs = {
#         'query': query,
#     }
# crew_instance = SQLDeveloperCrew()
# crew_instance.crew().kickoff(inputs=inputs)
