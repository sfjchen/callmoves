import Link from "next/link";

/**
 * Renders the 404 page.
 */
export default function NotFound() {
  return (
    <div className="max-w-5xl text-sm">
      <h1 className="text-xl font-semibold mb-1">No such call</h1>
      <p className="text-neutral-500">
        Nothing is processed under that id.{" "}
        <Link href="/" className="underline underline-offset-2">Back to the library</Link>.
      </p>
    </div>
  );
}
