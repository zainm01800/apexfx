#!/bin/bash
# Starts Qwen local agent/model environment; NOT called by the live scanner loop.
export QWEN_MODEL="openai:claude-fable-5"
export OPENAI_API_BASE="https://aiprimetech.io/v1"
export OPENAI_API_KEY="sk-fcb08589608be913922f922c73f479a2cea31dee420c91ffb1937b73e960c4a9"
export OPENAI_MAX_CONTEXT=1000000

# Force Node to inject the custom undici network patch
NODE_OPTIONS="--import=/tmp/qwen-patch-undici.mjs" qwen
