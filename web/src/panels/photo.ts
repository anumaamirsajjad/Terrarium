/**
 * Turning a chosen file into what the observations endpoint takes.
 *
 * Its own module rather than a helper inside the panel: a file that exports both a
 * component and a function loses React Fast Refresh, and this is the half worth testing
 * anyway.
 */

/** The largest photo worth sending. Bigger ones are the camera's fault, not the user's. */
export const MAX_PHOTO_BYTES = 5 * 1024 * 1024;

/**
 * Strip the `data:image/jpeg;base64,` prefix a FileReader adds.
 *
 * The API wants raw base64 and validates it, so leaving the prefix on would produce a 422
 * about invalid base64 for a file that was perfectly fine.
 */
export function stripDataUri(dataUri: string): string {
  const comma = dataUri.indexOf(",");
  return comma === -1 ? dataUri : dataUri.slice(comma + 1);
}

/** Read a File as a data URI. Wrapped so the panel can `await` it. */
export function readAsDataUri(file: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error("could not read that file"));
    reader.readAsDataURL(file);
  });
}
