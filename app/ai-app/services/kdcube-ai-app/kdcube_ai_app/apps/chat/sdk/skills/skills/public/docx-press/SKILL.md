---
name: docx-press
description: |
  Teaches agents how to author Markdown that renders cleanly into DOCX, with
  heading structure (up to 6 levels), tables, citations, and embedded images.
version: 2.0.0
category: document-creation
tags:
  - docx
  - markdown
  - tables
  - citations
  - images
  - headings
when_to_use:
  - Generating Markdown for write_docx
  - Building structured DOCX reports with deep heading hierarchies
  - Including citations and a references section
  - Embedding images in documents
author: kdcube
created: 2026-01-16
updated: 2026-01-23
namespace: public
import:
  - internal.link-evidence
  - internal.sources-section
---

# DOCX Authoring for Reports

## Overview
This skill teaches how to produce Markdown that renders cleanly into DOCX.
Use consistent heading levels (up to 6 levels deep), compact spacing, valid tables,
and embedded images for professional documents.

## Core Rules
- Use headings (# through ######) to structure sections with proper hierarchy
- Deeper headings (####, #####, ######) are automatically indented and sized appropriately
- Prefer short paragraphs and concise lists
- Use pipe tables with a header row; keep column counts modest
- Avoid nested tables or complex HTML; keep to Markdown primitives
- Images use standard markdown syntax: ![alt text](path/to/image.png)

## Heading Hierarchy
The renderer supports 6 heading levels with automatic styling:

```markdown
# Top-level section (H1 - largest, used for major sections)
## Subsection (H2 - section breaks)
### Sub-subsection (H3 - grouped content)
#### Detailed point (H4 - indented, smaller)
##### Fine detail (H5 - more indented)
###### Finest detail (H6 - most indented, smallest)
```

**Best Practices:**
- Use ## for major section breaks (these create new document sections)
- Use ### and #### for hierarchical content within sections
- Don't skip levels (e.g., # followed by ###)
- Deeper headings (####, #####) are auto-indented for visual hierarchy

## Images
Include images using standard markdown syntax:

```markdown
![Core Architecture Diagram](diagrams/core-arch.png)
![Network Topology](images/network.png)
```

**Image Guidelines:**
- Images are centered with 6-inch max width (full page width)
- Alt text becomes the image caption (italic, centered, muted color)
- Use descriptive alt text for accessibility
- Images should be local files accessible during rendering
- Supported formats: PNG, JPEG, GIF, BMP

## Citations
- Use [[S:n]] tokens inline after factual claims
- If sources are required, include a References section at the end
- Only include web sources that exist in the sources pool

## Recommended Structure

```markdown
# Document Title

## Executive Summary
Brief overview with key points [[S:1]].

## Background and Context

### Historical Development
Description of how the situation evolved.

#### Early Phase (2015-2020)
Detailed analysis [[S:2]].

#### Recent Developments (2021-2025)
Additional detail [[S:3]].

### Current State
Overview of present conditions.

## Methodology

### Data Collection Approach
Explanation of research methods.

#### Survey Design
Details about participant selection and questions.

#### Analysis Framework
Statistical methods and tools used.

## Key Findings

![Chart showing trend data](charts/trends.png)

## Quantitative Results

| Variable | Mean | Std Dev | Significance |
| --- | --- | --- | --- |
| Response Time | 2.3s | 0.4s | p < 0.01 |
| Accuracy | 94% | 3% | p < 0.05 |
| User Satisfaction | 4.2/5 | 0.6 | p < 0.01 |

## Discussion

### Interpretation of Results

#### Unexpected Outcomes
Analysis of surprising findings.

#### Alignment with Hypothesis
How results support or refute predictions.

### Limitations

#### Sample Size Constraints
- Limited geographic diversity
- Recruitment challenges
- Time constraints

#### Methodological Considerations
Potential sources of bias.

## Recommendations

### Short-term Actions
- Action item 1
- Action item 2

### Long-term Strategy
Detailed recommendations.

## References
1. Source title (S:1)
2. Source title (S:2)
```

## Example with Deep Headings

```markdown
# Product Launch Strategy

## Market Analysis

### Target Demographics

Understanding our customer segments drives product positioning and messaging.

#### Primary Segment: Tech-Savvy Professionals

- **Age Range**: 28-45 years
- **Income**: $75K-$150K annually
- **Key Behaviors**: Early adopters, value efficiency

##### Digital Engagement Patterns
- Mobile-first preference (78% of interactions)
- Active on LinkedIn and Twitter
- Consume video content during commutes

##### Purchase Decision Factors
- Peer recommendations (weighted 45%)
- Online reviews and ratings (weighted 30%)
- Brand reputation (weighted 25%)

#### Secondary Segment: Small Business Owners

##### Operational Priorities
- Cost-effectiveness is primary concern
- Integration with existing tools critical
- Scalability for growth

##### Research Behavior
Research cycle typically 4-6 weeks before purchase decision.

###### Information Sources
- Industry blogs and forums
- Comparison websites
- Free trial experiences

### Competitive Landscape

#### Direct Competitors

##### Market Leader: CompanyX
- **Market Share**: 34%
- **Strengths**: Brand recognition, extensive features
- **Weaknesses**: Complex pricing, steep learning curve

##### Emerging Challenger: StartupY
- **Market Share**: 12%
- **Strengths**: Modern UX, aggressive pricing
- **Weaknesses**: Limited integrations, new brand

## Product Architecture

### Core Platform Components

#### Frontend Layer

##### Web Application
Built with React 18 for responsive, real-time user experience.

##### Mobile Applications
- iOS: Swift 5.9, minimum iOS 15
- Android: Kotlin, minimum API level 26

#### Backend Services

##### API Gateway
Rate limiting, authentication, and request routing.

###### Authentication Methods
- OAuth 2.0 for third-party integrations
- SAML for enterprise SSO
- API keys for programmatic access

## Launch Timeline

### Phase 1: Beta Release (Q1 2026)

#### Week 1-2: Limited Beta
- 100 hand-selected users
- Focus on core workflow validation
- Daily feedback sessions

#### Week 3-6: Expanded Beta
- 1,000 users via waitlist
- A/B testing of key features
- Performance monitoring

### Phase 2: Public Launch (Q2 2026)

#### Pre-Launch Activities
- Press kit distribution
- Influencer partnerships
- Content marketing campaign

## Performance Metrics

### Q1 2026 Beta Results

| Metric | Target | Actual | Status |
| --- | --- | --- | --- |
| Sign-ups | 1,000 | 1,247 | ✓ Exceeded |
| Daily Active Users | 60% | 73% | ✓ Exceeded |
| NPS Score | 40+ | 52 | ✓ Exceeded |
| Conversion Rate | 8% | 11% | ✓ Exceeded |

## Visual Assets

![User Journey Map](diagrams/user-journey.png)

![Feature Adoption Funnel](charts/adoption-funnel.png)

## Budget Allocation

### Marketing Spend by Channel

| Channel | Q1 Budget | Q2 Budget | ROI Target |
| --- | --- | --- | --- |
| Content Marketing | $45K | $60K | 4:1 |
| Paid Social | $80K | $120K | 3:1 |
| Influencer Partnerships | $30K | $50K | 5:1 |
| Events & Webinars | $25K | $40K | 2:1 |

## Risk Assessment

### Technical Risks

#### Infrastructure Scalability
**Likelihood**: Medium | **Impact**: High

##### Mitigation Strategies
- Load testing at 10x expected traffic
- Auto-scaling configuration
- CDN deployment for static assets

### Market Risks

#### Competitive Response
**Likelihood**: High | **Impact**: Medium

##### Monitoring Indicators
- Competitor pricing changes
- Feature announcements
- Marketing campaign intensity

## References
```

## Formatting Tips

**Lists:**
- Use `-` or `*` for bullets
- Use `1.` for numbered lists
- Indent with 2 spaces per level for nested lists

**Emphasis:**
- Use **bold** for important terms and labels
- Use *italic* for emphasis or notes
- Combine for ***very important*** (though rarely needed)

**Tables:**
- Keep columns to 3-5 for readability
- Use clear, concise headers
- Align numbers right, text left (handled automatically)
- Include units in headers (e.g., "Revenue ($M)")

**Code:**
- Use triple backticks for code blocks: ```python
- Specify language for syntax awareness
- Keep code samples concise and relevant

## Common Pitfalls to Avoid

❌ **Don't skip heading levels:**
```markdown
# Title
### Subsection  ← BAD: skipped H2
```

✅ **Do maintain hierarchy:**
```markdown
# Title
## Section
### Subsection
```

❌ **Don't use HTML for structure:**
```markdown
<div><h4>Title</h4></div>  ← BAD
```

✅ **Do use markdown headings:**
```markdown
#### Title  ← GOOD
```

❌ **Don't overuse deep nesting:**
```markdown
###### Six levels deep everywhere  ← BAD: hard to read
```

✅ **Do use 2-4 levels typically:**
```markdown
## Section
### Subsection
#### Detail  ← GOOD: clear hierarchy
```