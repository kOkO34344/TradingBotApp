import { redirect } from "next/navigation";

// FTMO is the landing screen, not Charts.
//
// Charts is an IBKR screen, and rule 9 retired IBKR for new orders — so
// landing there greets you with a wall of Gateway connection errors from a
// venue that is deliberately no longer trading. Worse, it makes a dead
// Gateway look like the application is broken. FTMO is the venue that can
// actually trade and it does not depend on Gateway at all.
export default function Home() {
  redirect("/ftmo");
}
