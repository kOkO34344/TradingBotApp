import { redirect } from "next/navigation";

// Charts is the landing screen — it was the first thing asked for, and it's
// the one screen that still works when the account is flat.
export default function Home() {
  redirect("/charts");
}
