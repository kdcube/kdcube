# Bundle Examples

This folder contains versioned, runnable bundle examples.

## Getting started
- [bundle-dev-README.md](../../../../../../../../docs/sdk/bundle/bundle-dev-README.md)

## Examples
- [ReAct Agent](react%402026-02-10-02-44): General purpose ReAct agent equipped with [react-tools-README.md](../../../../../../../../docs/sdk/agents/react/react-tools-README.md) tools (read/write/memsearch/hide/file_search/patch), web tools (search/fetch) and rendering tools (write_pdf|docx|png|pptx). More on it [react](../../../../../../../../docs/sdk/agents/react)
- [ReAct Doc Reader](react.doc%402026-03-02-22-10): React agent with a **bundle-defined knowledge space** (`ks:`) and `react.search_knowledge` tool. It builds a local index and exposes docs/sources via shared bundle local storage. See [bundle-knowledge-space-README.md](../../../../../../../../docs/sdk/bundle/bundle-knowledge-space-README.md).
- [Iso Runtime Demo](with-isoruntime%402026-02-16-14-00): demonstrates the code execution in iso runtime (`docker`, `fargate`).
- [App with economics](eco%402026-02-18-15-06): derives from [BaseEntrypointWithEconomics](../../solutions/chatbot/entrypoint_with_economic.py) and demonstrates economics gateway application. 
