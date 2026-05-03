import sys
import os
import unittest
from unittest.mock import patch, MagicMock

# Add the scripts directory so we can import the repair script
sys.path.append(os.path.join(os.path.dirname(__file__), "../../scripts"))
import ai_autorepair


class TestAIAutoRepair(unittest.TestCase):
    @patch("ai_autorepair.requests.get")
    @patch("ai_autorepair.genai.Client")
    @patch("ai_autorepair.os.environ.get")
    def test_main_success(self, mock_env_get, mock_genai_client, mock_requests_get):
        # Mock environment variables
        env_vars = {
            "GEMINI_API_KEY": "fake_key",
            "GITHUB_TOKEN": "fake_token",
            "WORKFLOW_RUN_ID": "123",
            "REPO_OWNER": "test_owner",
            "REPO_NAME": "test_repo",
            "WORKSPACE_DIR": os.path.dirname(
                os.path.dirname(os.path.dirname(__file__))
            ),
        }
        mock_env_get.side_effect = lambda k, d=None: env_vars.get(k, d)

        # Mock the GitHub API response for jobs
        mock_jobs_response = MagicMock()
        mock_jobs_response.status_code = 200
        mock_jobs_response.json.return_value = {
            "jobs": [
                {
                    "name": "test_job",
                    "conclusion": "failure",
                    "url": "http://fake-job-url",
                }
            ]
        }

        # Mock the GitHub API response for the log text
        mock_log_response = MagicMock()
        mock_log_response.status_code = 200
        mock_log_response.text = "Error in core/events.py: Line 10 KeyError"

        # requests.get is called twice: first for jobs, then for the job log
        mock_requests_get.side_effect = [mock_jobs_response, mock_log_response]

        # Mock Gemini Client and its response
        mock_client_instance = MagicMock()
        mock_genai_client.return_value = mock_client_instance
        mock_generate_response = MagicMock()

        # Provide a fake AI response that includes a markdown code block
        mock_generate_response.text = "```python:core/events.py\n# Fixed fake code\n```"
        mock_client_instance.models.generate_content.return_value = (
            mock_generate_response
        )

        # We also need to patch os.path.exists and open so we don't actually overwrite files
        with patch("ai_autorepair.os.path.exists") as mock_exists, patch(
            "builtins.open", new_callable=unittest.mock.mock_open, read_data="old code"
        ) as mock_open:

            # Pretend any file ending in .py exists so it gets processed
            mock_exists.return_value = True

            # Execute the main function
            ai_autorepair.main()

            # Assertions
            mock_open.assert_any_call(
                os.path.join(env_vars["WORKSPACE_DIR"], "core/events.py"),
                "r",
                encoding="utf-8",
            )
            # Check w if it wrote
            mock_open.assert_any_call(
                os.path.join(env_vars["WORKSPACE_DIR"], "core/events.py"),
                "w",
                encoding="utf-8",
            )
            handle = mock_open()
            handle.write.assert_called_with("# Fixed fake code")


if __name__ == "__main__":
    unittest.main()
