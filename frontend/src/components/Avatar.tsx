import { initials, seatColor } from "../split";

/** A person's circle: initials on their seat color, with the name always written underneath. */
export function Avatar({ name, seat, size = "md" }: { name: string; seat: number; size?: "sm" | "md" | "lg" }) {
  return (
    <span className={`avatar avatar-${size}`} style={{ backgroundColor: seatColor(seat) }} aria-hidden="true">
      {initials(name)}
    </span>
  );
}
