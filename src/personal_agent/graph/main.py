from __future__ import annotations

from typing import Any

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
        graph.add_node("prepare_shared_scores", self.nodes.prepare_shared_scores)
        graph.add_node("run_editorial_arm", self.nodes.run_editorial_arm)
        graph.add_node("run_opportunity_arm", self.nodes.run_opportunity_arm)
        graph.add_node("merge_story_scores", self.nodes.merge_story_scores)
        graph.add_node("categorize_stories", self.nodes.categorize_stories)
        graph.add_node("summarize_digests", self.nodes.summarize_digests)
        graph.add_node("publish_digests", self.nodes.publish_digests)
        graph.add_node("persist_results", self.nodes.persist_results)

        graph.set_entry_point("fetch_story_sources")
        graph.add_edge("fetch_story_sources", "deduplicate_story_sources")
        graph.add_edge("deduplicate_story_sources", "fetch_story_details")
        graph.add_edge("fetch_story_details", "prepare_shared_scores")
        graph.add_edge("prepare_shared_scores", "run_editorial_arm")
        graph.add_edge("prepare_shared_scores", "run_opportunity_arm")
        graph.add_edge("run_editorial_arm", "merge_story_scores")
        graph.add_edge("run_opportunity_arm", "merge_story_scores")
        graph.add_edge("merge_story_scores", "categorize_stories")
        graph.add_edge("categorize_stories", "summarize_digests")
        graph.add_edge("summarize_digests", "publish_digests")
        graph.add_edge("publish_digests", "persist_results")
        graph.add_edge("persist_results", END)
        return graph

    def get_graph(self) -> Any:
        return self.graph.get_graph()

    def draw_ascii(self) -> str:
        return self.get_graph().draw_ascii()

    def draw_mermaid(self) -> str:
        return self.get_graph().draw_mermaid()

    def draw_png(self) -> bytes:
        graph = self.get_graph()
        png_bytes = graph.draw_png()
        if png_bytes is not None:
            return png_bytes
        return graph.draw_mermaid_png()

    async def run(self, request: HNWorkflowRequest) -> HNWorkflowState:
        initial_state = HNWorkflowState(request=request)
        return await self.graph.ainvoke(initial_state)
