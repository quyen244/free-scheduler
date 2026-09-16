/**
 * Resolve the two specifiers a route file uses that plain node does not know.
 *
 * Next resolves `@/...` through jsconfig and `next/server` through its own
 * bundler. Testing a route handler without a bundler means teaching node the
 * same two rules, which is cheaper than moving the handler's logic somewhere
 * artificial just so it can be imported.
 */

const root = new URL("../", import.meta.url);

export async function resolve(specifier, context, next) {
  if (specifier === "next/server") return next("next/server.js", context);
  if (specifier.startsWith("@/")) {
    const path = specifier.slice(2);
    const file = /\.[a-z]+$/.test(path) ? path : `${path}.js`;
    return next(new URL(`src/${file}`, root).href, context);
  }
  return next(specifier, context);
}
