const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "viewer_request.js"), "utf8");
const context = vm.createContext({});
vm.runInContext(source, context);

function request(uri, method = "GET") {
  return context.handler({ request: { uri, method } });
}

test("rewrites the root and extensionless GET/HEAD routes", () => {
  assert.equal(request("/").uri, "/index.html");
  assert.equal(request("/courses/123").uri, "/index.html");
  assert.equal(request("/account/", "HEAD").uri, "/index.html");
});

test("preserves static files and non-static methods", () => {
  assert.equal(request("/assets/app.js").uri, "/assets/app.js");
  assert.equal(request("/favicon.ico").uri, "/favicon.ico");
  assert.equal(request("/courses/123", "POST").uri, "/courses/123");
});
