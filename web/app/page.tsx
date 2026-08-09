import { redirect } from "next/navigation";

// Watch is the landing screen. It is the only one that answers the question
// you open this app to ask — what did the robot do overnight, and how close is
// the account to a limit. Landing on a chart made a dead IB Gateway look like
// a broken application; landing on the venue that actually trades does not.
export default function Home() {
  redirect("/watch");
}
