# Architecture boundary

This repository is a domain agent hosted by Agent Runtime.

It owns only:

- audience and topic inputs;
- WeChat 题图 copy constraints;
- traffic-feedback interpretation;
- local content packaging.

It must consume, rather than reimplement, Agent Runtime facilities for identity,
goals, durable memory, context projection, model backends, authorization,
evidence, recovery, and execution policy.

The first release has no publishing actuator. Its only actuator is a local,
copy-ready content package for a human operator.
