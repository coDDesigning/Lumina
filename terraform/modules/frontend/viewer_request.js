function handler(event) {
  var request = event.request;

  if (request.method !== "GET" && request.method !== "HEAD") {
    return request;
  }

  var uri = request.uri;
  var finalSegment = uri.substring(uri.lastIndexOf("/") + 1);

  if (uri === "/" || uri.endsWith("/") || !finalSegment.includes(".")) {
    request.uri = "/index.html";
  }

  return request;
}
