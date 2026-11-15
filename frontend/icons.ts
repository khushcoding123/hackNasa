// Small, consistent line icons for workspace controls. Labels remain in the markup.
const paths:Record<string,string>={
 search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/>',
 layers:'<path d="m12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5"/>',
 rectangle:'<rect x="4" y="5" width="16" height="14" rx="2"/>',
 pen:'<path d="m15 4 5 5M4 20l5-1L20 8a2 2 0 0 0-5-5L4 14l-1 7 6-2"/>',
 target:'<circle cx="12" cy="12" r="7"/><circle cx="12" cy="12" r="2"/><path d="M12 2v3m0 14v3M2 12h3m14 0h3"/>',
 terrain:'<path d="m3 19 6-11 4 6 3-5 5 10H3Z"/>',
};
export const icon=(name:string)=>`<svg class="ui-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name]??paths.target}</svg>`;
