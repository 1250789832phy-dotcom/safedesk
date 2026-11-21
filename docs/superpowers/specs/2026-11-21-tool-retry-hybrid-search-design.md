# Tool Budgets and Local Retrieval

## Accepted Scope

Implement the user's tool retry, call budget, local hash embedding and hybrid
retrieval requirements in the existing `agent/` and `context/` packages.
Keep the existing keyword search default and structured validation failures.

## Tool Execution

`ToolRegistry.execute` counts logical calls, validates registration and arguments,
then delegates to `_call_with_retry` and `execute_inner`. Defaults are 25 calls,
10 seconds per wait and three attempts with 0.5/1 second backoff. A caller's
remaining task duration bounds all waits and backoff together.

Python threads cannot be forcibly stopped. A timeout retries waiting for the same
unfinished Future instead of starting an overlapping invocation. A completed
invocation raising OSError can be executed again. Missing files and permission
failures remain terminal. Exhaustion raises ToolError with the original cause.
Executor shutdown does not wait for a timed-out worker; that worker may still
finish later. This is a waiting deadline, not execution cancellation.

Service submission resets calls after the idempotency check. Service and fixed
test API callers translate ToolError into the existing structured failure format.
The fixed-test helper serializes reset and execution with the same settings lock
as task submission, so concurrent repairs cannot erase a running task's count.

## Retrieval

Casefolded character unigrams and bigrams are mapped by CRC32 into 256 signed
count buckets and L2-normalized, using only the standard library. All three index
factory entry points default to this provider; explicit None disables vectors.

Embedding mode reports local_hash_embedding. Hybrid mode combines half the
keyword score divided by the maximum candidate keyword score and half the cosine
score mapped from [-1, 1] to [0, 1]. Only candidates with a keyword hit or positive
cosine similarity are eligible; return at most five. Keyword mode retains its
existing behavior and keyword_fallback label. Hash similarity is lexical,
not learned semantic similarity.

## Verification

Preserve the 117-test baseline, updating only the old embedding mode label
assertion for the explicitly requested contract change. Add timeout, transient
retry, call budget/reset, invalid input, service failure, factory default,
determinism/normalization and meaningful hybrid ranking tests. Run the full suite
and the in-memory evaluation; no real model or credentials are needed.
