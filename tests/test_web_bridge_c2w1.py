from __future__ import annotations

from pathlib import Path
from unittest import mock

from icp_renov_contracts.services import ContractRegisterFilter
from icp_renov_contracts.ui.web_host import UiBridge

from test_document_generation import GenerationCase


class WebBridgeC2W1Tests(GenerationCase):
    def setUp(self) -> None:
        super().setUp()
        self.second = self.contracts.create_draft()
        self.terminal = self.contracts.create_draft()
        self.context.lifecycle.abandon(self.terminal.id)
        self.actions: list[str] = []
        self.bridge = UiBridge(self.context, self.actions.append)
        self.states: list[dict] = []
        self.bridge.stateChanged.connect(self.states.append)

    def state(self) -> dict:
        self.bridge.refresh()
        return self.states[-1]

    def test_initial_snapshot_is_bounded_and_python_authoritative(self) -> None:
        before = tuple(self.context.lifecycle.history(self.contract.id))
        state = self.state()
        self.assertEqual(len(state["rows"]), 3)
        self.assertEqual(state["action_count"], 2)
        self.assertEqual(len(state["filters"]), 5)
        self.assertEqual(set(state["rows"][0]), {
            "id", "number", "updated", "client", "site", "status", "status_code",
            "deadline", "signal", "needs_action", "document",
        })
        self.assertEqual(tuple(self.context.lifecycle.history(self.contract.id)), before)

    def test_all_five_filters_and_search_are_served_by_the_python_register(self) -> None:
        expected = {
            ContractRegisterFilter.ALL: 3,
            ContractRegisterFilter.ACTIONS: 2,
            ContractRegisterFilter.DRAFTS: 2,
            ContractRegisterFilter.ACTIVE: 0,
            ContractRegisterFilter.TERMINAL: 1,
        }
        for selected, count in expected.items():
            self.bridge.setFilter(selected.value)
            state = self.states[-1]
            self.assertEqual(len(state["rows"]), count)
            if selected is ContractRegisterFilter.ACTIONS:
                self.assertEqual(len(state["rows"]), state["action_count"])
        self.bridge.setFilter(ContractRegisterFilter.ALL.value)
        self.bridge.setSearch("no-match")
        self.assertEqual(self.states[-1]["rows"], [])
        self.bridge.setSearch("")
        self.assertEqual(len(self.states[-1]["rows"]), 3)

    def test_web_search_focus_restoration_is_conditional_and_shared(self) -> None:
        source = (Path(__file__).parents[1] / "src" / "icp_renov_contracts" / "ui_web" / "app.js").read_text(encoding="utf-8")
        for identifier in ("contracts-search", "clients-search"):
            self.assertIn(identifier, source)
        for primitive in (
            "function captureSearchFocus()", "function restoreSearchFocus(focus)",
            "document.activeElement", "selectionStart", "selectionEnd", "selectionDirection",
            "input.setSelectionRange(start, end, focus.direction)",
            "if (!focus) return;", "restoreSearchFocus(searchFocus)",
        ):
            self.assertIn(primitive, source)
        self.assertIn("Math.min(Math.max(0, focus.start), length)", source)
        self.assertIn("Math.min(Math.max(start, focus.end), length)", source)

    def test_explicit_actions_open_the_bounded_web_workspace(self) -> None:
        opened = self.bridge.openContract(self.contract.id)
        self.assertTrue(opened["ok"])
        self.assertEqual(self.bridge.page_name, "CONTRACT_WORKSPACE")
        self.assertEqual(self.bridge.contract_id, self.contract.id)
        self.bridge.returnToContracts()
        created = self.bridge.createContract()
        self.assertTrue(created["ok"])
        self.assertEqual(self.bridge.page_name, "CONTRACT_WORKSPACE")
        self.assertEqual(self.bridge.contract_id, created["id"])
        self.bridge.navigate("CLIENTS")
        self.bridge.navigate("SETTINGS")
        self.bridge.navigate("unknown")
        self.assertEqual(self.actions, ["SETTINGS"])
        self.assertEqual(self.bridge.page_name, "CLIENTS")
        with mock.patch.object(self.context.backup, "create_now") as create_now:
            self.bridge.saveBackup()
        create_now.assert_called_once_with()
        self.assertTrue(self.states)
