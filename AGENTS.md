# Repository instructions

## Pull request review verification

For every pull request review, verify the exact PR head after the review's static safety pass:

- From `backend/`, run `python -m pytest -q` in an isolated project environment.
- From `frontend/`, run `npm ci` followed by `npm run build` with a Node.js version supported by the project. Run any frontend test script the project adds later.

Run these checks even when the PR changes only one part of the app. If a required tool is missing, set up a compatible version when practical. Report each command's result and the reviewed commit in the review. If a check cannot run safely, explain why, mark it unverified, and do not call the PR merge-ready.
