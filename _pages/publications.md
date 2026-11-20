---
layout: page
permalink: /publications/
title: publications
nav: true
nav_order: 2
---

{% include bib_search.liquid %}

<div class="publications">

<h2 id="conference">Conference</h2>
{% bibliography --query @*[publication_group=conference] %}

<h2 id="workshop">Workshop</h2>
{% bibliography --query @*[publication_group=workshop] %}

<h2 id="journal">Journal</h2>
{% bibliography --query @*[publication_group=journal] %}

</div>
