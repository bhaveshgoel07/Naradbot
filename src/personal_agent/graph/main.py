from __future__ import annotations

from langgraph.graph import END, StateGraph

from personal_agent.graph.nodes.hn import HNWorkflowNodes
from personal_agent.graph.state import HNWorkflowRequest, HNWorkflowState


class HNWorkflow:
    """Human-readable LangGraph workflow for Hacker News ingestion and publishing."""

    def __init__(self, nodes: HNWorkflowNodes) -> None:
        self.nodes = nodes
        self.graph = self._build_graph().compile()

    def _build_graph(self):
        graph = StateGraph(HNWorkflowState)
        graph.add_node("fetch_story_sources", self.nodes.fetch_story_sources)
        graph.add_node("deduplicate_story_sources", self.nodes.deduplicate_story_sources)
        graph.add_node("fetch_story_details", self.nodes.fetch_story_details)
        graph.add_node("score_stories", self.nodes.score_stories)
        graph.add_node("categorize_stories", self.nodes.categorize_stories)
        graph.add_node("summarize_digests", self.nodes.summarize_digests)
        graph.add_node("publish_digests", self.nodes.publish_digests)
        graph.add_node("persist_results", self.nodes.persist_results)

        graph.set_entry_point("fetch_story_sources")
        graph.add_edge("fetch_story_sources", "deduplicate_story_sources")
        graph.add_edge("deduplicate_story_sources", "fetch_story_details")
        graph.add_edge("fetch_story_details", "score_stories")
        graph.add_edge("score_stories", "categorize_stories")
        graph.add_edge("categorize_stories", "summarize_digests")
        graph.add_edge("summarize_digests", "publish_digests")
        graph.add_edge("publish_digests", "persist_results")
        graph.add_edge("persist_results", END)
        return graph

    async def run(self, request: HNWorkflowRequest) -> HNWorkflowState:
        initial_state = HNWorkflowState(request=request)
        return await self.graph.ainvoke(initial_state)
