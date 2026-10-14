"""The live feed page: a local web page that shows a match record as it is written.

`python -m ucla_agent.feed` serves one page on 127.0.0.1 that lists the
model's choices with everything sent to the model and what came back, the plan
timeline, the other events of the match and, for Red Alert, the match figures
and the order throughput. It only reads the record: it writes nothing, and
never talks to the agent or a model server.
"""
