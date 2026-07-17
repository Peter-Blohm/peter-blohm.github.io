---
layout: post
title: "Temperature Scaling in Discrete Sequence Models"
date: 2026-07-17
tags: [paper, icml]
---

Our ICML 2026 paper, [Temperature Scaling in Discrete Sequence (Language) Models](https://openreview.net/forum?id=bHIeH7450V), studies sequence-level temperature scaling for autoregressive and diffusion-based language models.

Temperature scaling is a common way to control generation behavior, but applying it token by token can distort the distribution over full sequences. This becomes especially delicate for discrete diffusion language models, where multiple generation orderings make likelihood estimation high-variance. We introduce a unified formalism for sequence models, partition-free metrics for measuring effective temperature, and fine-tuning objectives that reliably move models toward a target sequence-level temperature.

Full write-up coming soon.

- [PDF](https://openreview.net/pdf?id=bHIeH7450V)
- [Code](https://github.com/Aalto-QuML/temp_lm)
