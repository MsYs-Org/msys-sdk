// Dependency-free Node/Electron-side example for msys-mipc-stdio.
// stdout is the protocol stream; diagnostics must use stderr.
"use strict";

const readline = require("node:readline");

function send(message) {
  process.stdout.write(`${JSON.stringify(message)}\n`);
}

const component = process.env.MSYS_COMPONENT_ID || "org.example.node:main";
const generation = Number.parseInt(process.env.MSYS_GENERATION || "0", 10);

const input = readline.createInterface({
  input: process.stdin,
  crlfDelay: Infinity,
  terminal: false,
});

input.on("line", (line) => {
  let message;
  try {
    message = JSON.parse(line);
  } catch (error) {
    console.error(`invalid mIPC input: ${error.message}`);
    process.exitCode = 65;
    input.close();
    return;
  }

  if (message.type === "welcome") {
    send({ type: "subscribe", topic: "msys.lifecycle.*" });
    send({ type: "ready" });
    return;
  }
  if (message.type === "shutdown") {
    input.close();
    return;
  }
  if (message.type === "call") {
    if (message.method === "ping") {
      send({
        type: "return",
        id: message.id,
        payload: { ok: true, runtime: "node", echo: message.payload || {} },
      });
    } else {
      send({
        type: "error",
        id: message.id,
        code: "NO_METHOD",
        message: String(message.method || ""),
      });
    }
  }
});

input.on("close", () => {
  process.exit(process.exitCode || 0);
});

send({ type: "hello", component, generation });
