import {ChatTurn} from "./chatTypes.ts";
import {CanvasArtifact, CanvasArtifactType} from "../logExtensions/canvas/types.ts";

const mdExample = `# Project Overview

## Introduction

This is a **sample document** demonstrating common Markdown elements. It covers *most* of what you'll need for everyday writing.

---

## Features

- Lightweight and readable
- Renders in browsers, editors, and GitHub
- Supports **bold**, *italic*, and \`inline code\`

## Getting Started

1. Install a Markdown editor
2. Create a file with the \`.md\` extension
3. Start writing!

## Code Example
\`\`\`python
def greet(name):
    return f"Hello, {name}!"
\`\`\`

## Comparison Table

| Feature     | Supported |
|-------------|-----------|
| Headings    | ✅        |
| Tables      | ✅        |
| Images      | ✅        |
| Video       | ❌        |

## Resources

> "Markdown is intended to be as easy-to-read and easy-to-write as is feasible."
> — John Gruber

Check out the [official spec](https://daringfireball.net/projects/markdown/) for more details.

![Alt text for an image](https://example.com/image.png)
`

const htmlExample = `
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>iFrame Content</title>
  <style>
    body {
      font-family: sans-serif;
      display: flex;
      justify-content: center;
      align-items: center;
      height: 100vh;
      margin: 0;
      background: #f0f4ff;
    }
    .card {
      background: white;
      border-radius: 12px;
      padding: 2rem;
      box-shadow: 0 4px 20px rgba(0,0,0,0.1);
      text-align: center;
    }
    h1 { color: #333; margin: 0 0 0.5rem; }
    p  { color: #666; margin: 0; }
  </style>
</head>
<body>
  <div class="card">
    <h1>👋 Hello from iframe!</h1>
    <p>This content is loaded via <code>srcdoc</code>.</p>
  </div>
</body>
</html>
`

export const exampleConversationData: {
    conversationId: string
    conversationTitle: string
    turnOrder: string[]
    turns: {
        [key: string]: ChatTurn
    }
} = {
    turnOrder: ["example_turn_0"],
    turns: {
        "example_turn_0": {
            id: "example_turn_0",
            state: "finished",
            userMessage: {text:"Example user message", timestamp:Date.now(), attachments:[{name:"example.txt", size: 42}]},
            followUpQuestions: ["There is no spoon", "Test"],
            events:[],
            steps:{},
            artifacts: [
                {
                    artifactType: CanvasArtifactType,
                    timestamp: Date.now(),
                    content: {
                        name: "canvas_md_example",
                        title: "Canvas Markdown example",
                        content: mdExample,
                        contentType: "markdown",
                    },
                    canCopy: true,
                    canSave: true,
                    complete: true,
                } as CanvasArtifact,
                {
                    artifactType: CanvasArtifactType,
                    timestamp: Date.now(),
                    content: {
                        name: "canvas_html_example",
                        title: "Canvas HTML example",
                        content: htmlExample,
                        contentType: "html",
                    },
                    canCopy: true,
                    canSave: true,
                    complete: true,
                } as CanvasArtifact
            ]
        }
    },
    conversationId: "example_conversation",
    conversationTitle: "Example Conversation",
}