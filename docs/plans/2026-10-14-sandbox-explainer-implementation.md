# Sandbox Approaches Explainer Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Deliver an accurate, offline HTML decision guide for Agent Smith sandbox choices.

**Architecture:** One semantic `docs/sandbox-approaches.html` file with inline CSS and small progressive-enhancement JS for a comparison selector. All content remains available without JS. No project code or sandbox behavior changes.

**Tech Stack:** HTML5, CSS, vanilla JavaScript, Python 3.10 standard-library static checks.

---

### Task 1: Content and structure

**Files:** Create `docs/sandbox-approaches.html`.

1. Add title, status disclaimer, architecture diagram, seven approaches, PDF-required control checklist, decision factors, recommendation and source links to local PDF/spec/plan.
2. State that no sandbox/providers are implemented and current tests are fake-loop tests, not security tests.
3. Compare Python-only safeguards with OS-enforced boundaries; distinguish Docker container from VM; qualify evaluator availability and standard-library constraint.

### Task 2: Design and accessibility

**Files:** Modify `docs/sandbox-approaches.html`.

1. Add responsive inline CSS and distinctive editorial hierarchy. Use system serif and monospace fallback fonts only; no external assets.
2. Add an accessible approach selector with native buttons and aria-pressed, preserving all content without JS. Include print and prefers-reduced-motion rules.
3. Add small JS to highlight the selected approach; no network calls or sandbox execution.

### Task 3: Verify

**Files:** Verify `docs/sandbox-approaches.html` only.

1. Run a Python `html.parser` smoke check for required sections, semantic document shape, no external resources, and all seven approach anchors.
2. If a browser is available, inspect desktop/mobile layout, keyboard controls, print behavior. Do not assert browser verification if unavailable.
3. No git commit step: the current working directory is not a Git repository.
