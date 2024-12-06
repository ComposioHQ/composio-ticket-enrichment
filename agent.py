import operator
import os
import typing as t
from enum import Enum

from langchain_aws import ChatBedrock
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from prompts import TICKET_COMMENT_PROMPT, REPO_ANALYZER_PROMPT
from tenacity import retry, stop_after_attempt, wait_exponential
from langchain_core.runnables.graph import MermaidDrawMethod

from composio_langgraph import Action, App, ComposioToolSet, WorkspaceType


class Model(str, Enum):
    CLAUDE = "claude"
    OPENAI = "openai"

model = Model.CLAUDE


def add_thought_to_request(request: t.Dict[str, t.Any]) -> t.Dict[str, t.Any]:
    request["thought"] = {
        "type": "string",
        "description": "Provide the thought of the agent in a small paragraph in concise way. This is a required field.",
        "required": True,
    }
    return request


def pop_thought_from_request(request: t.Dict[str, t.Any]) -> t.Dict[str, t.Any]:
    request.pop("thought", None)
    return request

def print_graph(graph: CompiledStateGraph):
    # Import necessary modules
    import os
    from io import BytesIO

    from IPython.display import Image, display
    from PIL import Image

    # Generate the Mermaid PNG
    png_data = graph.get_graph().draw_mermaid_png(
        draw_method=MermaidDrawMethod.API,
    )

    # Create a PIL Image from the PNG data
    image = Image.open(BytesIO(png_data))

    # Save the image
    output_path = "workflow_graph.png"
    image.save(output_path)


def get_graph(repo_path):
    toolset = ComposioToolSet(
        workspace_config=WorkspaceType.Host(),
        metadata={
            App.CODE_ANALYSIS_TOOL: {
                "dir_to_index_path": repo_path,
            }
        },
        processors={
            "pre": {
                App.FILETOOL: pop_thought_from_request,
                App.CODE_ANALYSIS_TOOL: pop_thought_from_request,
            },
            "schema": {
                App.GITHUB: add_thought_to_request,
                App.FILETOOL: add_thought_to_request,
                App.CODE_ANALYSIS_TOOL: add_thought_to_request,
            },
        },
    )

    repo_analyzer_tools = [
        *toolset.get_tools(
            actions=[
                Action.CODE_ANALYSIS_TOOL_GET_CLASS_INFO,
                Action.CODE_ANALYSIS_TOOL_GET_METHOD_BODY,
                Action.CODE_ANALYSIS_TOOL_GET_METHOD_SIGNATURE,
                Action.CODE_ANALYSIS_TOOL_GET_RELEVANT_CODE,
                Action.FILETOOL_LIST_FILES,
                Action.FILETOOL_OPEN_FILE,
                Action.FILETOOL_SCROLL,
                Action.FILETOOL_FIND_FILE,
                Action.FILETOOL_SEARCH_WORD,
            ]
        )
    ]

    comment_on_ticket_tools = [
        *toolset.get_tools(
            actions=[
                Action.LINEAR_CREATE_LINEAR_COMMENT
            ]
        )
    ]

    if model == Model.CLAUDE:
        client = ChatBedrock(
            # credentials_profile_name="default",
            model_id="anthropic.claude-3-5-sonnet-20241022-v2:0",
            region_name="us-west-2",
            model_kwargs={"temperature": 0, "max_tokens": 8192},
        )
    else:
        client = ChatOpenAI(
            model="gpt-4-1106-preview",
            temperature=0,
            max_completion_tokens=4096,
            api_key=os.environ["OPENAI_API_KEY"],
        )

    class AgentState(t.TypedDict):
        messages: t.Annotated[t.Sequence[BaseMessage], operator.add]
        sender: str

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
    )
    def invoke_with_retry(agent, state):
        return agent.invoke(state)

    def create_agent_node(agent, name):
        def agent_node(state):
            if model == Model.CLAUDE and isinstance(state["messages"][-1], AIMessage):
                state["messages"].append(HumanMessage(content="Placeholder message"))

            try:
                result = invoke_with_retry(agent, state)
            except Exception as e:
                print(f"Failed to invoke agent after 3 attempts: {str(e)}")
                result = AIMessage(
                    content="I apologize, but I encountered an error and couldn't complete the task. Please try again or rephrase your request.",
                    name=name,
                )
            if not isinstance(result, ToolMessage):
                if isinstance(result, dict):
                    result_dict = result
                else:
                    result_dict = result.dict()
                result = AIMessage(
                    **{
                        k: v
                        for k, v in result_dict.items()
                        if k not in ["type", "name"]
                    },
                    name=name,
                )
            return {"messages": [result], "sender": name}

        return agent_node

    def create_agent(system_prompt, tools):
        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", system_prompt),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )
        llm = client
        if tools:
            return prompt | llm.bind_tools(tools)
        else:
            return prompt | llm

    repo_analyzer_agent_name = "Repo-Analyzer-Agent"
    repo_analyzer_agent = create_agent(REPO_ANALYZER_PROMPT, repo_analyzer_tools)
    repo_analyzer_agent_node = create_agent_node(
        repo_analyzer_agent, repo_analyzer_agent_name
    )

    comment_on_ticket_agent_name = "Comment-On-Ticket-Agent"
    comment_on_ticket_agent = create_agent(TICKET_COMMENT_PROMPT, comment_on_ticket_tools)
    comment_on_ticket_agent_node = create_agent_node(
        comment_on_ticket_agent, comment_on_ticket_agent_name
    )

    workflow = StateGraph(AgentState)

    workflow.add_edge(START, repo_analyzer_agent_name)
    workflow.add_node(repo_analyzer_agent_name, repo_analyzer_agent_node)
    workflow.add_node(comment_on_ticket_agent_name, comment_on_ticket_agent_node)
    workflow.add_node("repo_analyzer_tools_node", ToolNode(repo_analyzer_tools))
    workflow.add_node("comment_on_ticket_tools_node", ToolNode(comment_on_ticket_tools))

    def repo_analyzer_router(
        state,
    ) -> t.Literal["repo_analyzer_tools_node", "continue", "comment_on_ticket"]:
        messages = state["messages"]
        for message in reversed(messages):
            if isinstance(message, AIMessage):
                last_ai_message = message
                break
        else:
            last_ai_message = messages[-1]

        if last_ai_message.tool_calls:
            return "repo_analyzer_tools_node"
        if "ANALYSIS COMPLETED" in last_ai_message.content:
            return "comment_on_ticket"
        return "continue"

    workflow.add_conditional_edges(
        "repo_analyzer_tools_node",
        lambda x: x["sender"],
        {repo_analyzer_agent_name: repo_analyzer_agent_name},
    )
    workflow.add_conditional_edges(
        repo_analyzer_agent_name,
        repo_analyzer_router,
        {
            "continue": repo_analyzer_agent_name,
            "repo_analyzer_tools_node": "repo_analyzer_tools_node",
            "comment_on_ticket": comment_on_ticket_agent_name,
        },
    )

    def comment_on_ticket_router(
        state,
    ) -> t.Literal["comment_on_ticket_tools_node", "continue", "__end__"]:
        messages = state["messages"]
        for message in reversed(messages):
            if isinstance(message, AIMessage):
                last_ai_message = message
                break
        else:
            last_ai_message = messages[-1]

        if last_ai_message.tool_calls:
            return "comment_on_ticket_tools_node"
        if "REVIEW COMPLETED" in last_ai_message.content:
            return "__end__"
        return "continue"

    workflow.add_conditional_edges(
        "comment_on_ticket_tools_node",
        lambda x: x["sender"],
        {comment_on_ticket_agent_name: comment_on_ticket_agent_name},
    )
    workflow.add_conditional_edges(
        comment_on_ticket_agent_name,
        comment_on_ticket_router,
        {
            "continue": comment_on_ticket_agent_name,
            "comment_on_ticket_tools_node": "comment_on_ticket_tools_node",
            "__end__": END,
        },
    )

    graph = workflow.compile()
    return graph, toolset

if __name__ == '__main__':
    graph, _ = get_graph("/home/ubuntu/composio/")
    print_graph(graph)