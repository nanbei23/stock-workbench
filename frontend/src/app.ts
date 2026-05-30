import { getJson, postJson, requestJson } from "./lib/api";

export const ApiClient = {
  get: getJson,
  post: postJson,
  request: requestJson
};

declare global {
  interface Window {
    ApiClient: typeof ApiClient;
  }
}

window.ApiClient = ApiClient;
