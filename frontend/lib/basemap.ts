import type { Theme } from "@/lib/theme";

// CARTO's two neutral basemaps, named ONCE. dark_all is what every map in this
// app has always sat on; light_all is CARTO's published light twin -- same
// host, same {s}/{z}/{x}/{y}{r} template, same attribution. A map component
// asks for the URL with the current theme and swaps it on the LIVE layer
// (L.TileLayer#setUrl) when the theme changes, so the basemap follows the
// chrome without remounting the map or losing the reader's view.
export function basemapTileUrl(theme: Theme): string {
  const style = theme === "light" ? "light_all" : "dark_all";
  return `https://{s}.basemaps.cartocdn.com/${style}/{z}/{x}/{y}{r}.png`;
}
