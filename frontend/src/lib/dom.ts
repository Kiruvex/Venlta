export function qs<T extends HTMLElement = HTMLElement>(selector: string, parent: ParentNode = document): T | null {
  return parent.querySelector<T>(selector);
}

export function qsAll<T extends HTMLElement = HTMLElement>(selector: string, parent: ParentNode = document): T[] {
  return Array.from(parent.querySelectorAll<T>(selector));
}

export function toggleClass(el: HTMLElement, className: string, force?: boolean): void {
  el.classList.toggle(className, force);
}

export function delegate(parent: HTMLElement, eventType: string, selector: string, handler: (e: Event, target: HTMLElement) => void): () => void {
  const listener = (e: Event) => {
    const target = (e.target as HTMLElement).closest<HTMLElement>(selector);
    if (target && parent.contains(target)) {
      handler(e, target);
    }
  };
  parent.addEventListener(eventType, listener);
  return () => parent.removeEventListener(eventType, listener);
}

export function stopEvent(e: Event): void {
  e.stopPropagation();
  e.preventDefault();
}
