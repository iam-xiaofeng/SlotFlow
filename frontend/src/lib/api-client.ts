export async function requestOk(
  failure: string,
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  const response = await fetch(input, init);
  if (!response.ok) {
    throw new Error(`${failure}: ${response.status}`);
  }
  return response;
}

export async function requestJson<T>(
  failure: string,
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<T> {
  return (await requestOk(failure, input, init)).json() as Promise<T>;
}
