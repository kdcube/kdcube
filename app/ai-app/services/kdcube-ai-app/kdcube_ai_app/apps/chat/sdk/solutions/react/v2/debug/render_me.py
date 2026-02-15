import asyncio
import json

from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.proto import RuntimeCtx
from kdcube_ai_app.apps.chat.sdk.solutions.react.v2.timeline import Timeline

runtime = RuntimeCtx()
timeline_path = "/private/tmp/ctx_v2_a8en15mb/out/timeline.json"
timeline_path = "/private/tmp/ctx_v2_gejt514p/out/timeline.json"
timeline_path = "/private/tmp/ctx_v2_umzyxass/out/timeline.json"
with open(timeline_path, "r") as f:
    timeline_dict = json.loads(f.read())

timeline = Timeline.from_payload(payload=timeline_dict,
                                 runtime=runtime)
async def render():
    blocks = await timeline.render()
    txts = []
    for b in blocks:
        if b.get("type") == "text":
            txts.append(b.get("text"))
        else:
            txts.append(f"Block type: {b.get('type')}, content: {b.get('content')}")
    txts = "\n".join(txts)
    print(txts)
    with open("rendered.txt", "w") as f:
        f.write(txts)

asyncio.run(render())