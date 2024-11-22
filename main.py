from agent import get_graph
from langchain_core.messages import HumanMessage
import traceback
from composio import Action, ComposioToolSet
from composio.utils.logging import get_logger

listener = ComposioToolSet().create_trigger_listener()
logger = get_logger(__name__)

# Triggers when a new event takes place
@listener.callback(filters={"trigger_name": "LINEAR_ISSUE_CREATED_TRIGGER"})
def callback_function(event):
    try: 
        logger.info("Received Linear issue created trigger")
        payload = event.payload
        action = payload.get("action")
        data = payload.get("data", {})
        project = data.get("project", {})
        project_name = project.get("name")
        number = data.get("number")
        
        if not project_name:
            logger.warning(f"Issue #{number} skipped: Not assigned to any project")
            return
        
        if project_name == "Python SDK":
            repo_owner, repo_name = "ComposioHQ", "composio"
        elif project_name == "Tech Infra":
            repo_owner, repo_name = "ComposioHQ", "hermes"
        else:
            logger.warning(f"Issue #{number} skipped: Project '{project_name}' not supported")
            return

        if action != "create":
            logger.debug(f"Issue #{number} skipped: Action '{action}' is not 'create'")
            return

        id = data.get("id")
        title = data.get("title")
        description = data.get("description")
        
        logger.info(f"Processing issue #{number} - {title} ({repo_owner}/{repo_name})")
    
        run_agent(id, title, description, repo_owner, repo_name)

    except Exception as e:
        logger.error(f"Error processing issue #{number}: {str(e)}")
        logger.debug(f"Full traceback:\n{traceback.format_exc()}")


def run_agent(id, title, description, repo_owner, repo_name) -> None:
    repo_path = f"/home/user/{repo_name}"

    graph, composio_toolset = get_graph(repo_path)

    composio_toolset.execute_action(
        action=Action.FILETOOL_CHANGE_WORKING_DIRECTORY,
        params={"path": "/".join(repo_path.split("/")[:-1])},
    )

    composio_toolset.execute_action(
        action=Action.FILETOOL_GIT_CLONE,
        params={"repo_name": f"{repo_owner}/{repo_name}"},
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
                    content=f"You have {repo_owner}/{repo_name} cloned at your current working directory. \
You have a linear ticket with: \
id: `{id}`, \
title: `{title}`, \
description: `{description}`. \
Your task is to find what files would be a good starting point to solve this issue. \
Also enrich the ticket with a comment that is helpful."
                )
            ]
        },
        {"recursion_limit": 50},
    )


if __name__ == "__main__":
    listener.wait_forever()
