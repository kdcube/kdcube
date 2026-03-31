---
name: preferences
id: preferences
description: |
  Use stored per-user preferences before personalizing the answer.
  The bundle captures preference observations during the chat and exposes them
  through the preferences tool surface.
version: 1.0.0
category: product-knowledge
tags:
  - preferences
  - personalization
  - bundle-local
when_to_use:
  - The user asks for a personalized recommendation or answer
  - The user refers to prior likes, dislikes, or formatting preferences
  - The agent wants to keep responses consistent with stored user preferences
author: kdcube
created: 2026-03-31
namespace: product
---

# Preference Skill

Before giving a personalized answer, check whether the bundle already knows a relevant preference.

Use:
- `preferences.get_preferences(recency, kwords)` to inspect stored preferences
- `preferences.set_preference(key, value, source)` only when the user explicitly states a durable preference

Rules:
- Do not claim a preference exists unless the tool returned it.
- Prefer a small keyword-filtered lookup before broad personalization.
- If no preference is stored, continue normally instead of fabricating one.
