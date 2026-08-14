import { redirect } from "next/navigation";

// Unit #22 (2026-08-14 directive): the front door opens onto the PROJECT
// OVERVIEW, not the dashboard. The dashboard is stage ③ (시뮬·보정) of the
// pipeline -- a place a consultant lands mid-work, not the page that orients
// them to the project as a whole. /overview composes the four-stage summary
// (network, data, sim, report) and is itself the one screen that decides
// whether there is even a project to summarize (see its own no-project
// branch), so it is the only honest first stop.
export default function HomePage() {
  redirect("/overview");
}
