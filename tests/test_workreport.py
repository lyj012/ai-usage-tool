import unittest

from workreport import (
    associate_ai_git,
    build_daily_report,
    classify_path,
    detect_rework,
    detect_topics,
    render_daily_markdown,
)


class WorkreportRulesTest(unittest.TestCase):
    def test_classify_path(self):
        self.assertEqual(classify_path("src/main/App.vue"), "frontend")
        self.assertEqual(classify_path("src/main/service/UserService.java"), "backend")
        self.assertEqual(classify_path("db/migration/V1__init.sql"), "sql")
        self.assertEqual(classify_path("README.md"), "doc")
        self.assertEqual(classify_path("config/app.yml"), "config")
        self.assertEqual(classify_path("tests/test_app.py"), "test")

    def test_build_daily_report_core_fields(self):
        ai_records = [
            {
                "project": "demo",
                "project_cwd": "C:/repo/demo",
                "session_id": "s1",
                "input_at": "2026-06-15T09:00:00+08:00",
                "task_finished_at": "2026-06-15T09:10:00+08:00",
                "input_text": "修改 payment service 和 order sql",
                "input_preview": "修改 payment service 和 order sql",
                "ai_active_seconds": 600,
                "after_done_gap_seconds": 120,
            }
        ]
        commits = [
            {
                "project": "demo",
                "repo_path": "C:/repo/demo",
                "hash": "abc123",
                "short_hash": "abc123",
                "parents": ["p1"],
                "committed_at": "2026-06-15T09:20:00+08:00",
                "message": "feat: update payment service",
                "is_merge": False,
                "files_changed": 2,
                "insertions": 10,
                "deletions": 2,
                "modules": ["payment"],
                "file_summaries": [
                    {"path": "src/payment/PaymentService.java", "insertions": 8, "deletions": 1},
                    {"path": "db/order.sql", "insertions": 2, "deletions": 1},
                ],
            },
            {
                "project": "demo",
                "repo_path": "C:/repo/demo",
                "hash": "merge123",
                "short_hash": "merge123",
                "parents": ["p1", "p2"],
                "committed_at": "2026-06-15T10:00:00+08:00",
                "message": "Merge branch main",
                "is_merge": True,
                "files_changed": 0,
                "insertions": 0,
                "deletions": 0,
                "modules": [],
                "file_summaries": [],
            },
        ]
        file_changes = [
            {
                "project": "demo",
                "path": "src/payment/PaymentService.java",
                "category": "backend",
                "module": "payment",
                "insertions": 8,
                "deletions": 1,
            },
            {
                "project": "demo",
                "path": "db/order.sql",
                "category": "sql",
                "module": "db",
                "insertions": 2,
                "deletions": 1,
            },
        ]
        report = build_daily_report(
            "2026-06-15",
            "tester",
            ai_records,
            commits,
            file_changes,
            {"accepted": True, "actual_result": ""},
            [],
        )
        self.assertEqual(report["overview"]["commit_count"], 2)
        self.assertEqual(report["overview"]["business_commit_count"], 1)
        self.assertIn("unmatched_ai_sessions", report)
        self.assertIn("commit_association_summary", report)
        self.assertTrue(report["associations"])

    def test_association_uses_path_overlap_evidence(self):
        ai_records = [
            {
                "project": "demo",
                "project_cwd": "C:/repo/demo",
                "session_id": "s1",
                "input_at": "2026-06-15T09:00:00+08:00",
                "task_finished_at": "2026-06-15T09:05:00+08:00",
                "input_text": "处理 payment controller",
                "input_preview": "处理 payment controller",
            }
        ]
        commits = [
            {
                "project": "demo",
                "repo_path": "C:/repo/demo",
                "hash": "abc123",
                "short_hash": "abc123",
                "committed_at": "2026-06-15T09:10:00+08:00",
                "message": "feat: update flow",
                "file_summaries": [{"path": "src/payment/PaymentController.java"}],
            }
        ]
        associations = associate_ai_git(ai_records, commits)
        evidence = associations[0]["matched_commits"][0]["evidence"]
        self.assertTrue(any("文件路径" in item for item in evidence))

    def test_rework_topics_and_markdown(self):
        ai_records = [
            {
                "project": "demo",
                "session_id": "s1",
                "input_at": "2026-06-15T09:00:00+08:00",
                "input_text": "fix redis payment bug",
                "input_preview": "fix redis payment bug",
            },
            {
                "project": "demo",
                "session_id": "s1",
                "input_at": "2026-06-15T09:05:00+08:00",
                "input_text": "fix redis payment bug again",
                "input_preview": "fix redis payment bug again",
            },
        ]
        commits = [
            {
                "project": "demo",
                "short_hash": "fix123",
                "hash": "fix123",
                "message": "fix payment bug",
                "committed_at": "2026-06-15T09:10:00+08:00",
                "insertions": 3,
                "deletions": 1,
                "file_summaries": [{"path": "src/payment/pay.py", "insertions": 3, "deletions": 1}],
            }
        ]
        file_changes = [
            {
                "project": "demo",
                "path": "src/payment/pay.py",
                "module": "payment",
                "category": "backend",
                "commit_hash": "fix123",
            }
        ]
        rework = detect_rework(ai_records, commits, file_changes, [])
        self.assertTrue(all({"type", "confidence", "evidence"}.issubset(row.keys()) for row in rework))
        self.assertTrue(any(row["type"] == "similar_inputs_same_session" for row in rework))
        topics = detect_topics(ai_records, commits, file_changes)
        self.assertTrue(any(row["topic"] == "Redis" for row in topics))
        markdown = render_daily_markdown(
            {
                "date": "2026-06-15",
                "overview": {},
                "project_distribution": [],
                "ai_usage": {},
                "git_workload": {},
                "associations": [],
                "rework_and_exceptions": rework,
                "technical_topics": topics,
                "quality_metrics": {"note": "估算"},
                "warnings": [],
            }
        )
        self.assertIn("规则估算", markdown)
        self.assertIn("返工和异常", markdown)


if __name__ == "__main__":
    unittest.main()
