#!/usr/bin/env node

/**
 * Sandcastle Runner - Node.js wrapper for executing sandcastle runs.
 * Called by the Python backend via subprocess.
 * 
 * Supports:
 * - Single agent runs
 * - Multi-agent parallel execution
 * - Structured output extraction
 * - Real-time log streaming via stdout
 */

import { parseArgs } from "node:util";
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { resolve, dirname } from "node:path";

// Parse command line arguments
const { values } = parseArgs({
  options: {
    config: { type: "string" },
    "run-id": { type: "string" },
    mode: { type: "string", default: "single" }, // single | parallel
  },
  strict: false,
});

if (!values.config) {
  console.error("Error: --config argument is required");
  process.exit(1);
}

// Read configuration
const configPath = resolve(values.config);
const config = JSON.parse(readFileSync(configPath, "utf-8"));

// Helper to create sandbox provider
async function createSandboxProvider(providerType, dockerImage) {
  switch (providerType) {
    case "docker": {
      const { docker } = await import("@ai-hero/sandcastle/sandboxes/docker");
      return docker(dockerImage ? { imageName: dockerImage } : {});
    }
    case "podman": {
      const { podman } = await import("@ai-hero/sandcastle/sandboxes/podman");
      return podman(dockerImage ? { imageName: dockerImage } : {});
    }
    case "vercel": {
      const { vercel } = await import("@ai-hero/sandcastle/sandboxes/vercel");
      return vercel();
    }
    case "no-sandbox":
    default: {
      const { noSandbox } = await import("@ai-hero/sandcastle/sandboxes/no-sandbox");
      return noSandbox();
    }
  }
}

// Helper to create agent provider
async function createAgentProvider(sandcastle, providerType, options = {}) {
  switch (providerType) {
    case "codex": {
      const { codex } = sandcastle;
      return codex(options);
    }
    case "cursor": {
      const { cursor } = sandcastle;
      return cursor(options);
    }
    case "pi": {
      const { pi } = sandcastle;
      return pi(options);
    }
    case "opencode": {
      const { opencode } = sandcastle;
      return opencode(options);
    }
    case "copilot": {
      const { copilot } = sandcastle;
      return copilot(options);
    }
    case "claude-code":
    default: {
      const { claudeCode } = sandcastle;
      return claudeCode(options);
    }
  }
}

// Execute a single run
async function executeRun(sandcastle, config, runConfig) {
  const { run } = sandcastle;
  
  const sandboxProvider = await createSandboxProvider(
    config.sandbox_provider,
    config.docker_image
  );
  
  const agentOptions = {};
  if (config.model) {
    agentOptions.model = config.model;
  }
  
  const agentProvider = await createAgentProvider(
    sandcastle,
    config.agent_provider,
    agentOptions
  );
  
  const runOptions = {
    agent: agentProvider,
    sandbox: sandboxProvider,
    prompt: runConfig.prompt,
    maxIterations: config.max_iterations || 1,
    idleTimeoutSeconds: config.idle_timeout_seconds || 600,
  };
  
  // Add branch strategy if specified
  if (runConfig.branch_name) {
    runOptions.branchStrategy = {
      type: "branch",
      branch: runConfig.branch_name,
    };
  } else if (config.branch_strategy) {
    runOptions.branchStrategy = { type: config.branch_strategy };
  }
  
  // Add logging to file
  const logDir = resolve(config.project_path, ".sandcastle", "logs");
  mkdirSync(logDir, { recursive: true });
  const logFile = resolve(logDir, `run-${runConfig.run_id || Date.now()}.log`);
  
  runOptions.logging = {
    type: "file",
    path: logFile,
    verbose: true,
  };
  
  // Add structured output if specified
  if (runConfig.output_schema) {
    const { Output } = sandcastle;
    runOptions.output = Output.object({
      tag: runConfig.output_tag || "result",
      schema: runConfig.output_schema,
    });
  }
  
  // Execute the run
  const result = await run(runOptions);
  
  return {
    iterations: result.iterations?.length || 0,
    commits: result.commits || [],
    branch: result.branch || null,
    completionSignal: result.completionSignal || null,
    output: result.output || null,
    logFile,
  };
}

// Execute parallel runs
async function executeParallelRuns(sandcastle, config, runs) {
  const { run } = sandcastle;
  
  // Create shared sandbox if requested
  if (config.use_shared_sandbox) {
    const { createSandbox } = sandcastle;
    const sandboxProvider = await createSandboxProvider(
      config.sandbox_provider,
      config.docker_image
    );
    
    const agentOptions = {};
    if (config.model) {
      agentOptions.model = config.model;
    }
    
    const agentProvider = await createAgentProvider(
      sandcastle,
      config.agent_provider,
      agentOptions
    );
    
    await using sandbox = await createSandbox({
      branch: config.shared_branch || "agent/parallel",
      sandbox: sandboxProvider,
    });
    
    // Execute all runs on the same sandbox
    const results = [];
    for (const runConfig of runs) {
      const result = await sandbox.run({
        agent: agentProvider,
        prompt: runConfig.prompt,
        maxIterations: config.max_iterations || 1,
      });
      results.push({
        run_id: runConfig.run_id,
        iterations: result.iterations?.length || 0,
        commits: result.commits || [],
        branch: result.branch || null,
      });
    }
    
    return { mode: "shared-sandbox", results };
  }
  
  // Execute runs in parallel on separate sandboxes
  const promises = runs.map(async (runConfig) => {
    try {
      const result = await executeRun(sandcastle, config, runConfig);
      return { run_id: runConfig.run_id, ...result, status: "completed" };
    } catch (error) {
      return {
        run_id: runConfig.run_id,
        status: "failed",
        error: error.message,
      };
    }
  });
  
  const results = await Promise.allSettled(promises);
  return {
    mode: "parallel",
    results: results.map((r) => r.status === "fulfilled" ? r.value : r.reason),
  };
}

async function main() {
  try {
    // Dynamically import sandcastle
    const sandcastle = await import("@ai-hero/sandcastle");
    
    let output;
    
    if (values.mode === "parallel" && config.runs) {
      // Multi-agent parallel execution
      output = await executeParallelRuns(sandcastle, config, config.runs);
    } else {
      // Single run
      output = await executeRun(sandcastle, config, config);
    }
    
    // Output result as JSON
    console.log(JSON.stringify(output));
    process.exit(0);
  } catch (error) {
    console.error("Sandcastle run failed:", error.message);
    process.exit(1);
  }
}

main();