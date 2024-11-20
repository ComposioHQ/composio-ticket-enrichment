from agent import get_graph
from langchain_core.messages import HumanMessage
import traceback
from composio import Action, ComposioToolSet
from composio.utils.logging import get_logger

REPO_OWNER = "ComposioHQ"
REPO_NAME = "composio"

listener = ComposioToolSet().create_trigger_listener()
logger = get_logger(__name__)

# Triggers when a new event takes place
@listener.callback(filters={"trigger_name": "LINEAR_ISSUE_CREATED_TRIGGER"})
def callback_function(event):
    try: 
        logger.info(f"Received trigger LINEAR_ISSUE_CREATED_TRIGGER")
        payload = event.payload
        action = payload.get("action")
        data = payload.get("data", {})
        project = data.get("project", {})
        project_name = project.get("name")
        number = data.get("number")
        
        if not project_name or project_name != "Python SDK":
            logger.info(f"Skipping issue {number} as it is not in the Python SDK project")
            return

        if action != "create":
            logger.info(f"Skipping issue {number} as it is not a create event")
            return

        id = data.get("id")
        title = data.get("title")
        description = data.get("description")
        

        logger.info(f"Running ticket enrichment agent for issue {number}")
    
        run_agent(id, title, description)

    except Exception as e:
        traceback.print_exc()


def run_agent(id, title, description) -> None:
    repo_path = f"/home/user/{REPO_NAME}"

    graph, composio_toolset = get_graph(repo_path)

    composio_toolset.execute_action(
        action=Action.FILETOOL_CHANGE_WORKING_DIRECTORY,
        params={"path": "/".join(repo_path.split("/")[:-1])},
    )

    composio_toolset.execute_action(
        action=Action.FILETOOL_GIT_CLONE,
        params={"repo_name": f"{REPO_OWNER}/{REPO_NAME}"},
    )
    composio_toolset.execute_action(
        action=Action.FILETOOL_CHANGE_WORKING_DIRECTORY,
        params={"path": repo_path},
    )
    composio_toolset.execute_action(
        action=Action.CODE_ANALYSIS_TOOL_CREATE_CODE_MAP,
        params={},
    )

    run_result = graph.invoke(
        {
            "messages": [
                HumanMessage(
                    content=f"You have {REPO_OWNER}/{REPO_NAME} cloned at your current working directory. \
You have a linear ticket with id `{id}`, title `{title}` and description `{description}`. Your task is to find what files \
would be a good starting point to solve this issue. Also enrich the ticket with a comment that is helpful."
                )
            ]
        },
        {"recursion_limit": 50},
    )


if __name__ == "__main__":
    listener.wait_forever()
