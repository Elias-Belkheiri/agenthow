# Sandbox approaches explainer design

**Date:** 2026-10-14
**Status:** Approved in conversation

## Purpose

Explain all practical sandbox families for Agent Smith, their security boundaries and trade-offs, and the unresolved project decision. The page is an explanatory artifact, not a sandbox implementation or proof of security.

## Presentation

A single self-contained offline HTML document at `docs/sandbox-approaches.html`. Editorial technical aesthetic: warm paper background, near-black ink, signal orange and cool blue accents, monospace schematic labels, large serif display headings. Responsive on desktop and mobile; print-friendly. No scripts from third parties, external fonts, images, network requests, or generated-code execution.

The hero illustrates a model response entering a policy gate, a worker, and a trusted observation channel. A concise status band distinguishes built fake loop from unbuilt sandbox and providers. A selectable approach navigator shows seven approaches: no execution; in-process restrictions; Python subprocess; OS sandbox; container; VM/microVM or remote runner; WASM/WASI. Every section states what the boundary *actually* protects, what it cannot promise, prerequisites and Agent Smith fit. Explain the practical hybrid (restricted Python API inside OS isolation) and the PDF's standard-library-only security mechanism requirement and evaluator-runtime uncertainty. A closing decision panel compares best-effort subprocess vs OS/container requirement without claiming a decision was made.

Use semantic HTML headings, accessible buttons, reduced-motion support, keyboard focus, and progressive enhancement: every approach remains readable with JavaScript disabled. No markdown table is the deliverable; the page provides the visual comparison.

## Accuracy and verification

Source: `agentsmith.pdf` pages 14–19 and 34, `docs/specs/0001-stack-and-architecture.md`, `docs/plans/2026-10-14-agent-smith-first-milestone.md`. Clearly state that the test suite covers tools and fake loop, not any sandbox. Verify HTML structure, content assertions, zero external dependencies, and local rendering if a browser is available. This document is not a commitment to deploy Docker or any particular kernel feature.
