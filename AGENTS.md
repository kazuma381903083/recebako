# Recebako development instructions

## Source of truth

- Product specification: docs/spec/recebako-spec-v1.1.html
- Report appearance reference: docs/spec/monthly-report-sample.html
- Architectural decisions: docs/adr/
- Implement only the GitHub issue currently requested.

## Commands

- Install/sync: `uv sync`
- Test: `uv run pytest`
- Lint: `uv run ruff check .`
- Format check: `uv run ruff format --check .`
- Type check: `uv run mypy src`

Run all applicable checks before declaring work complete.

## Architecture rules

- Runtime data must live outside the Git repository.
- The application consumes files placed in the inbox; it must not depend directly on Syncthing.
- Keep Ollama communication inside `src/recebako/ai/`.
- Treat all LLM output as untrusted input.
- Validate structured output with Pydantic and business validation rules.
- Keep the raw item name and normalized item name separately.
- SQLite writes must use transactions.
- Processing must be idempotent and safe to retry.
- Bind the review UI only to `127.0.0.1`.

## Security rules

- Never commit real receipts, ledger databases, generated personal reports, or logs.
- Never call external AI or OCR services.
- Only localhost Ollama communication is allowed at runtime.
- Do not output receipt contents to application logs.
- Do not add a production dependency without explaining why.

## Testing rules

- CI tests must not require Ollama.
- Mock Ollama HTTP responses in unit and integration tests.
- Tests using a local model must use the `ollama` marker.
- Tests using real receipts must use the `private` marker and remain outside Git.
- Add a regression test for every bug fix.

## Done

A task is complete only when:

1. The requested scope is implemented.
2. Tests cover normal and failure cases.
3. Lint, formatting, typing, and tests pass.
4. No unrelated files are changed.
5. The final summary lists changed files and verification results.