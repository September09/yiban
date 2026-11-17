// MD drafts are local, ephemeral, and never become writable memories without the existing preview.
function createMarkdownImport(api){
  const s={jobs:[],reviews:[],hidden:[],running:false,serial:0,turn:null};
  const el=id=>document.getElementById(id);
  const add=(host,tag,value)=>{const n=document.createElement(tag);if(value!==undefined)n.textContent=value;host.append(n);return n;};
  function stop(){
    s.serial++;if(s.turn)fetch('/api/turn/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({turn_id:s.turn})}).catch(()=>{});
    api.abort();s.turn=null;s.running=false;el('v2MdStop').hidden=true;
  }
  function clear(){
    stop();for(const token of s.jobs)api.request('md/discard',{extraction_token:token}).catch(()=>{});
    s.jobs=[];s.reviews=[];s.hidden=[];el('v2MdReview').replaceChildren();el('v2MdFiles').value='';
    el('v2MdText').value='';el('v2MdName').value='';el('v2MdConsent').checked=false;el('v2MdAccept').disabled=true;el('v2MdProgress').textContent='';
  }
  function changed(){clearDraft();el('v2MdProgress').textContent='资料已变化，请重新提取；不会沿用旧候选。';}
  function clearDraft(){
    s.serial++;for(const token of s.jobs)api.request('md/discard',{extraction_token:token}).catch(()=>{});
    s.jobs=[];s.reviews=[];s.hidden=[];el('v2MdReview').replaceChildren();el('v2MdConsent').checked=false;el('v2MdAccept').disabled=true;api.invalidate();
  }
  function controls(locked){
    for(const id of ['v2MdFiles','v2MdName','v2MdText','v2MdExtract'])el(id).disabled=locked;
    el('v2MdAccept').disabled=locked||!s.reviews.length||!el('v2MdConsent').checked;
  }
  function render(){
    const host=el('v2MdReview');host.replaceChildren();
    s.reviews.forEach((review,index)=>{
      const section=add(host,'section');add(section,'h3','文章 '+(index+1)+' · 脱敏检查');
      add(section,'p',review.notice);
      const list=add(section,'details');add(list,'summary','逐段查看原文与移除范围（私人段落单独标注）');
      review.paragraphs.forEach(p=>{
        const article=add(list,'article');article.className='v2-memory-card';const label=add(article,'label'),box=add(label,'input');
        box.type='checkbox';box.checked=p.private;box.disabled=p.required;
        add(label,'strong',p.private?'私人／已移除段落':'将本段标为私人并从拟入库全文移除');
        add(article,'blockquote',p.text);
        box.onchange=()=>{
          if(box.checked)s.hidden[index].add(p.id);else s.hidden[index].delete(p.id);
          el('v2MdConsent').checked=false;api.invalidate();
          api.work(async(active,signal)=>{
            const result=await api.request('md/review',{extraction_token:s.jobs[index],private_paragraphs:[...s.hidden[index]]},signal);
            if(active()){s.reviews[index]=result;render();api.controls();}
          });
        };
      });
      const privateBox=add(section,'details');privateBox.open=true;add(privateBox,'summary','私人内容 · '+review.private_count+'段 · 本轮不入库');
      if(!review.private_count)add(privateBox,'p','没有自动识别到私人段落，请仍检查上方全文，必要时手动标记。');
      review.paragraphs.filter(p=>p.private).forEach(p=>add(privateBox,'blockquote',p.text));
      review.private_candidates.forEach(c=>{add(privateBox,'h4',c.title);add(privateBox,'p',c.summary);});
      const full=add(section,'details');add(full,'summary','拟入库来源全文 · 脱敏整理版');
      add(full,'pre',review.document.sources[0].text);
      const cards=add(section,'div');add(cards,'h4','公共候选与逐字依据 · '+review.document.records.length+'条');
      for(const record of review.document.records){
        const article=add(cards,'article');article.className='v2-memory-card';
        add(article,'strong',record.title+' · 待核实');add(article,'p',record.summary);
        record.evidence.forEach(e=>add(article,'blockquote',e.text));
      }
      review.warnings.forEach(w=>add(section,'p','待核对：'+w));
    });
  }
  async function extract(){
    if(s.running)return;
    clearDraft();const serial=s.serial;
    await api.work(async(active,signal)=>{
      s.running=true;el('v2MdStop').hidden=false;
      try{
        let files=[...(el('v2MdFiles').files||[])];
        const pasted=el('v2MdText').value.trim();
        if(files.length&&pasted)throw new Error('请选择文件或粘贴正文中的一种，避免同一文章重复提取。');
        if(!files.length&&pasted)files=[{name:el('v2MdName').value.trim()||'粘贴文章.md',content:pasted}];
        if(!files.length||files.length>5)throw new Error('请选择1–5篇MD，或粘贴一篇MD正文。每篇最多24000字。');
        for(let index=0;index<files.length;index++){
          const file=files[index];if(!/\.(md|markdown)$/i.test(file.name))throw new Error('仅支持MD文件。');
          if(file.size>96000)throw new Error('文件过大，请拆分后上传，没有截断内容。');
          let content=file.content;
          if(content===undefined){try{content=new TextDecoder('utf-8',{fatal:true}).decode(await file.arrayBuffer());}catch{throw new Error('MD文字编码无法识别，请另存为UTF-8后上传。');}}
          if(!active()||serial!==s.serial)return;
          const draft=await api.request('md/start',{name:file.name,content},signal);
          s.jobs.push(draft.extraction_token);s.hidden.push(new Set());
          for(let part=0;part<draft.parts;part++){
            if(!active()||serial!==s.serial)return;
            el('v2MdProgress').textContent='本机提取第 '+(index+1)+' / '+files.length+' 篇，第 '+(part+1)+' / '+draft.parts+' 段。尚未写入记忆。';
            s.turn=newTurnId();
            await api.request('md/next',{extraction_token:draft.extraction_token,turn_id:s.turn},signal);
          }
          const review=await api.request('md/review',{extraction_token:draft.extraction_token,private_paragraphs:[]},signal);
          if(!active()||serial!==s.serial)return;
          s.reviews.push(review);s.hidden[index]=new Set(review.paragraphs.filter(p=>p.private).map(p=>p.id));render();
        }
        el('v2MdProgress').textContent='提取完成，尚未保存。请检查公共候选、私人段落和脱敏全文，再生成保存候选。';
      }finally{s.running=false;s.turn=null;el('v2MdStop').hidden=true;}
    });
  }
  el('v2MdExtract').onclick=extract;
  el('v2MdStop').onclick=()=>{stop();api.invalidate();el('v2MdProgress').textContent='已停止提取；本轮候选未保存，可重新提取。';};
  for(const id of ['v2MdFiles','v2MdName','v2MdText'])el(id)[id==='v2MdFiles'?'onchange':'oninput']=changed;
  el('v2MdConsent').onchange=api.controls;
  el('v2MdAccept').onclick=()=>api.work(async(active,signal)=>{
    if(!el('v2MdConsent').checked||!s.reviews.length)throw new Error('请先检查脱敏全文并勾选确认。');
    const doc={schema:'yiban.memory-candidates.v2',batch_name:'MD自动提取 · 人工脱敏审阅',sources:[],entities:[],events:[],records:[]};
    for(const key of ['sources','entities','events','records']){
      const rows=s.reviews.flatMap(r=>r.document[key]),unique=new Map();
      for(const row of rows){if(unique.has(row.key)&&JSON.stringify(unique.get(row.key))!==JSON.stringify(row))throw new Error('多份文章出现相同编号但内容不同，请分开审阅。');unique.set(row.key,row);}
      doc[key]=[...unique.values()];
    }
    if(!doc.records.length)throw new Error('没有可导入的公共候选。私人内容仍暂缓入库。');
    const result=await api.request('validate',{document:doc},signal);if(active())api.setDocument(result);
  });
  return{clear,controls};
}
