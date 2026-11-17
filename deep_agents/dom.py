"""Only fixed synthetic UI labels leave Chromium; no input values or raw DOM."""
SANITIZED_DOM = r'''(prefix => {
    const knownLabels = new Set(['Account','Username','Password','Workspace','Member','Access key',
        'Continue securely','Enter CEP','Access workspace','Complete synthetic verification']);
    return [...document.querySelectorAll('input:not([type="hidden"]),button,a[href]')]
        .filter(el => el.getClientRects().length > 0).slice(0,20).flatMap((el,i) => {
            const tag=el.tagName.toLowerCase();
            const label=(el.getAttribute('aria-label') || (tag==='input' ? el.labels?.[0]?.textContent : el.textContent))?.trim();
            if (!knownLabels.has(label)) return [];
            let key=null, role=tag==='input' ? 'textbox' : (tag==='a' ? 'link' : 'button');
            if (tag==='input' && ['account','username','password'].includes(el.name)) key=el.name;
            else if (tag==='button' && ['Continue securely','Enter CEP'].includes(label)) key='submit';
            else if (tag==='button' && label==='Complete synthetic verification') key='challenge';
            else if (tag==='a' && label==='Access workspace' && new URL(el.href).origin===location.origin && new URL(el.href).pathname==='/login') key='entry';
            if (!key) return [];
            const token=prefix+'-'+i;
            el.setAttribute('data-pass1-element',token);
            const evidence=['VISIBLE','FIXTURE_KNOWN_LABEL',el.getAttribute('aria-label')?'ARIA_LABEL':(tag==='input'?'ASSOCIATED_LABEL':'SUBMIT_TEXT')];
            if(tag==='input')evidence.push('NAME_HINT');
            return [{key,role,label,evidence,selector:'[data-pass1-element="'+token+'"]',sensitive: ['account','username','password'].includes(key)}];
        });
})(PREFIX)'''
