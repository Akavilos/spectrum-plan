// 面板交互：读取后端数据，将编辑结果提交到对应接口，并更新当前视图。

        const { createApp, ref, onMounted, watch, computed, nextTick } = Vue;

        createApp({
            setup() {
                const initialized = ref(false);
                const statusMsg = ref('正在连接数据接口…');
                const fatalError = ref(null);
                const overview = ref({});
                const umos = ref([]);
                const currentTab = ref('entries');
                const filterUmo = ref('');
                const showHistory = ref(false);
                const dataList = ref([]);

                const sourceOpen = ref({});
                const sourceCache = ref({});

                const l3 = ref({ continuity: '', volatile: [], willingness: 0.5, plan: {} });

                const personDetail = ref(null);

                let bridge = null;

                const stats = [
                    { label: '任务与承诺', key: 'intents', level: '计划', tagClass: 'tag-l2', color: 'var(--primary)' },
                    { label: '待发队列记录', key: 'pending', level: '投递', tagClass: 'tag-l2', color: 'var(--primary)' },
                    { label: '长期认知', key: 'memories', level: '记忆', tagClass: 'tag-l3', color: 'var(--primary)' },
                    { label: '往来与日记', key: 'entries', level: '记录', tagClass: 'tag-l1', color: 'var(--primary)' }
                ];

                const tabs = [
                    { id:'entries', label:'往来记录', hint:'查看收到、发出和后台生成的记录；后台记录不等同于真实来信。' },
                    { id:'lives', label:'生活时间轴', hint:'全局的连续生活经历，记录外出、归家、入睡与醒来的边界。' },
                    { id:'persons', label:'人物档案', hint:'按用户身份查看跨会话共用的好感、信任和人物认知。' },
                    { id:'memories', label:'长期认知', hint:'按会话查看长期认知、原话来源与重要程度。' },
                    { id:'intents', label:'任务与承诺', hint:'查看、添加、改期或取消角色的任务与承诺。' },
                    { id:'pending', label:'待发消息', hint:'管理计划发送的消息；恢复待发前应核对是否已经送达。' },
                    { id:'worldbook', label:'世界书', hint:'维护全局背景条目、触发条件和注入位置。' },
                    { id:'director', label:'剧情导演', hint:'管理剧情片段、触发记录、伏笔线索和故事状态。' },
                    { id:'tools', label:'工具说明', hint:'查看已注册工具，补充角色使用工具时的说明。' }
                ];
                const searchText = ref('');
                const loadingData = ref(false);
                const loadError = ref('');
                const pageNumber = ref(1);
                const pageSize = 50;
                let dataRequest = 0;
                const activeTab = computed(() => tabs.find(t => t.id === currentTab.value));
                const globalTab = computed(() => ['lives','persons','director','tools','worldbook'].includes(currentTab.value));
                const pagedTab = computed(() => ['entries','persons','memories','intents','pending'].includes(currentTab.value));
                const matchesSearch = row => !searchText.value.trim() || JSON.stringify(row).toLocaleLowerCase().includes(searchText.value.trim().toLocaleLowerCase());
                const filteredDataList = computed(() => dataList.value.filter(matchesSearch));
                const filteredLives = computed(() => lifeList.value.filter(matchesSearch));
                const filteredTools = computed(() => toolsList.value.filter(matchesSearch));
                const statusLabel = value => ({waiting:'待执行',done:'已完成',cancelled:'已取消',sent:'已发送',failed:'失败',uncertain:'送达待确认'}[value] || value);
                const kindLabel = value => ({received:'收到的消息',delivered:'已发送消息',script:'后台日记',invalid_script:'已失效日记',summary:'摘要'}[value] || value);
                const changePage = delta => {pageNumber.value += delta; fetchData();};


                const dialog = ref(null);
                let focusBeforeDialog = null;
                watch(dialog, async value => {
                    if (value) {
                        focusBeforeDialog = document.activeElement;
                        await nextTick();
                        document.querySelector('.modal-card textarea, .modal-card button')?.focus();
                    } else { await nextTick(); focusBeforeDialog?.focus(); }
                });
                const trapDialog = event => {
                    const fields = Array.from(event.currentTarget.querySelectorAll('button,textarea,input,select')).filter(n => !n.disabled);
                    const first = fields[0], last = fields[fields.length - 1];
                    if (event.shiftKey && document.activeElement === first) {event.preventDefault(); last.focus();}
                    else if (!event.shiftKey && document.activeElement === last) {event.preventDefault(); first.focus();}
                };
                const toast = ref('');
                const toastIsError = ref(false);
                let toastTimer = null;

                const showToast = (msg, isErr) => {
                    toast.value = msg;
                    toastIsError.value = !!isErr;
                    if (toastTimer) clearTimeout(toastTimer);
                    toastTimer = setTimeout(() => { toast.value = ''; }, 2600);
                };

                const askText = (opts) => new Promise((resolve) => {
                    dialog.value = { kind: 'text', danger: false, ...opts, text: opts.value ?? '', resolve };
                });

                const askConfirm = (opts) => new Promise((resolve) => {
                    dialog.value = { kind: 'confirm', danger: true, ...opts, resolve };
                });

                const confirmDialog = () => {
                    const d = dialog.value;
                    if (!d) return;
                    dialog.value = null;
                    if (d.kind === 'text') d.resolve(d.text);
                    else d.resolve(true);
                };

                const closeAndCancel = () => {
                    const d = dialog.value;
                    dialog.value = null;
                    if (d) d.resolve(d.kind === 'text' ? null : false);
                };

                const api = {
                    get: async (path, p) => { const value = await bridge.apiGet(path, p); if (value?.status === 'error' || value?.ok === false) throw Error(value.message || value.error || '读取失败'); return value; },
                    post: async (path, b) => { const value = await bridge.apiPost(path, b); if (value?.status === 'error' || value?.ok === false) throw Error(value.message || value.error || '保存失败'); return value; }
                };

                const refresh = async () => {
                    try {

                        const [resO, resU] = await Promise.all([api.get('page/overview'), api.get('page/umos')]);
                        overview.value = (resO && resO.status === 'ok' && resO.data) || resO || {counts:{}};
                        umos.value = Array.isArray(resU) ? resU : (resU && resU.status === 'ok' ? resU.data : []);
                        await fetchData();
                        if (filterUmo.value) await fetchL3();
                    } catch (e) { loadError.value = e.message; }
                };

                const fetchData = async () => {
                    const requestId = ++dataRequest;
                    dataList.value = [];
                    loadingData.value = true; loadError.value = '';
                    try {


                    if (currentTab.value === 'lives') {
                        await fetchLives();
                        return;
                    }

                    if (currentTab.value === 'director') {
                        await fetchDirector();
                        return;
                    }

                    if (currentTab.value === 'tools') {
                        await fetchTools();
                        return;
                    }

                    if (currentTab.value === 'worldbook') {
                        await fetchWorldbook();
                        return;
                    }
                    const params = { umo: filterUmo.value, limit: pageSize, offset: (pageNumber.value - 1) * pageSize, all: showHistory.value ? '1' : '0' };
                    const res = await api.get(`page/${currentTab.value}`, params);
                    const items = (res && res.status === 'ok' && res.data && res.data.items)
                        || (res && Array.isArray(res.items) && res.items)
                        || []
                    ;
                    if (requestId === dataRequest) dataList.value = items;
                    } catch(e) { if (requestId === dataRequest) loadError.value = e.message; }
                    finally { if (requestId === dataRequest) loadingData.value = false; }
                };

                const toolsList = ref([]);
                const toolsSource = ref('');
                const toolsOrphans = ref([]);

                const fetchTools = async () => {
                    const r = await api.get('page/tools');
                    const d = (r && r.status === 'ok' && r.data) || r || {};
                    toolsList.value = d.tools || [];
                    toolsSource.value = d.source || '';
                    toolsOrphans.value = d.notes_orphan || [];
                };

                const editToolNote = async (t) => {

                    const v = await askText({
                        title: `写 ${t.name} 的用法`,
                        hint: '什么时候该用这个工具。留空 = 删掉这条，回到只用它自带的说明。',
                        value: t.note || '',
                        placeholder: '例：别人让我推歌、点歌、找歌时',
                        confirmText: '保存'
                    });
                    if (v === null) return;
                    if (await postOr('page/tools/note', { name: t.name, note: v }, '已保存')) {
                        await fetchTools();
                    }
                };

                const wbList = ref([]);
                const wbConfig = ref({});
                const wbBooks = ref([]);
                const wbBookId = ref(0);
                const wbSelection = ref({global_book:null,current_book:null,strategy:'order'});
                const wbImportMode = ref('new');
                const wbImportWarnings = ref([]);
                const wbPreviewText = ref('');
                const wbPreviewGeneration = ref('normal');
                const wbPreview = ref(null);
                const wbPositions = {core:'基础背景',l3:'生活与场景',person:'人物资料',turn:'本轮补充'};
                const wbCurrentBook = computed(() => wbBooks.value.find(book => book.id === wbBookId.value));
                const wbEdit = ref(null);                                
                const wbUseProb = ref(false);                                           

                const wbBlank = () => ({
                    id: 0, book_id:wbBookId.value, title: '', text: '', keys: '', secondary: '',
                    selective: 0, selective_logic: 'and_any', constant: 0,
                    order_value: 100, case_sensitive: 0, match_whole_words: 0,
                    use_regex: 0, probability: 100, group_name: '', group_weight: 1,
                    use_group_scoring: 0, prioritize: 0, vectorized: 0,
                    ignore_budget: 0, sticky: 0, cooldown: 0, delay: 0,
                    scan_depth: 0, exclude_recursion: 0, prevent_recursion: 0,
                    delay_until_recursion: 0, position: 'l3', enabled: 1, generation_triggers:'',match_persona:0,extensions:'{}'
                });

                const wbCards = computed(() => {
                    const e = wbEdit.value;
                    const rows = wbList.value.filter(row => row.book_id === wbBookId.value && (matchesSearch(row) || e?.id === row.id));
                    return (e && !e.id) ? [e, ...rows] : rows;
                });
                const wbIsOpen = (c) => !!(wbEdit.value && c.id === wbEdit.value.id);

                const toggleWbCard = (c) => {
                    if (wbIsOpen(c)) { wbEdit.value = null; return; }
                    wbEdit.value = Object.assign(wbBlank(), c);
                    wbUseProb.value = Number(c.probability) < 100;
                };

                const wbStateValue = (c) => c.constant ? 'constant' : (c.vectorized ? 'vectorized' : 'normal');
                const wbStateLabel = (c) => ({ constant: '🔵 常驻', vectorized: '🔗 语义' }[wbStateValue(c)] || '🟢 普通');
                const wbState = computed({
                    get: () => wbEdit.value ? wbStateValue(wbEdit.value) : 'normal',
                    set: (v) => {
                        const f = wbEdit.value;
                        if (!f) return;
                        f.constant = v === 'constant' ? 1 : 0;
                        f.vectorized = v === 'vectorized' ? 1 : 0;
                    }
                });

                const wbEstTokens = (s) => {
                    const chars = Array.from(String(s == null ? '' : s));
                    if (!chars.length) return 0;
                    const cjk = chars.filter(c => /[\u3400-\u9FFF\uF900-\uFAFF\u3040-\u30FF\uAC00-\uD7AF]/.test(c)).length;
                    return cjk + Math.floor((chars.length - cjk + 3) / 4);
                };
                const wbEditTokens = computed(() => wbEdit.value ? wbEstTokens(wbEdit.value.text) : 0);

                const fetchWorldbook = async () => {
                    try {
                        const r = await api.get('page/worldbook/list');

                        const d = (r && r.status === 'ok' && r.data) || r || {};
                        wbList.value = d.items || [];
                        wbConfig.value = d.config || {};
                        wbBooks.value = d.books || [];
                        wbSelection.value = d.selection || {global_book:null,current_book:null,strategy:'order'};
                        if (!wbBooks.value.some(book => book.id === wbBookId.value)) wbBookId.value = wbSelection.value.current_book || wbSelection.value.global_book || wbBooks.value[0]?.id || 0;
                    } catch (e) {
                        wbList.value = [];
                        showToast('读取世界书失败：' + (e?.message || e), true);
                    }
                };

                const addWorldbook = () => {
                    if (!wbBookId.value) return showToast('请先新建或导入一本世界书',true);
                    wbEdit.value = wbBlank();                            
                    wbUseProb.value = false;
                };
                const cancelWorldbook = () => { wbEdit.value = null; };

                const saveWorldbook = async () => {
                    const f = wbEdit.value;
                    if (!f) return;
                    if (!String(f.text || '').trim()) return showToast('正文不能为空', true);

                    const payload = Object.assign({}, f, {
                        probability: wbUseProb.value ? f.probability : 100
                    });
                    if (await postOr('page/worldbook/upsert', payload, '已保存')) {
                        wbEdit.value = null;
                        await fetchWorldbook();
                    }
                };

                const deleteWorldbook = async (c) => {
                    if (!c.id) { wbEdit.value = null; return; }                  
                    const ok = await askConfirm({
                        title: '删除条目',
                        message: `删除「${c.title || (c.text || '').slice(0, 20) || '这条'}」？`
                               + '它的定时状态与向量会一并删掉，不可撤销。',
                        confirmText: '删除'
                    });
                    if (!ok) return;
                    if (await postOr('page/worldbook/delete', { id: c.id }, '已删除')) {
                        if (wbIsOpen(c)) wbEdit.value = null;
                        await fetchWorldbook();
                    }
                };

                const toggleWorldbook = async (c) => {
                    if (!c.id) return;
                    await postOr('page/worldbook/toggle',
                        { id: c.id, enabled: c.enabled ? 0 : 1 }, '已切换');
                    await fetchWorldbook();
                };

                const changeWbBook = () => {wbEdit.value=null;wbPreview.value=null;searchText.value='';};
                const saveWbSelection = async () => {
                    if (await postOr('page/worldbook/books/select',wbSelection.value,'使用设置已保存，最多使用两本')) await fetchWorldbook();
                };
                const saveWbBook = async (edit=false) => {
                    const book=edit ? wbCurrentBook.value : null;
                    const name=await askText({title:edit?'重命名世界书':'新建世界书',value:book?.name||'',hint:'书名只用于管理，不会注入给角色。',confirmText:'保存'});
                    if (name===null) return;
                    try {const r=await api.post('page/worldbook/books/save',{id:book?.id,name,description:book?.description||''});wbBookId.value=r.data.id;await fetchWorldbook();changeWbBook();showToast('世界书已保存');} catch(e){showToast(e.message,true);}
                };
                const duplicateWbBook = async () => {
                    try {const r=await api.post('page/worldbook/books/duplicate',{id:wbBookId.value});wbBookId.value=r.data.id;await fetchWorldbook();changeWbBook();showToast('副本已创建');} catch(e){showToast(e.message,true);}
                };
                const deleteWbBook = async () => {
                    const book=wbCurrentBook.value;if(!book)return;
                    if(!await askConfirm({title:'删除整本世界书',message:`删除“${book.name}”及其中${book.count}条条目？会同时取消它的使用选择，无法撤销。`,confirmText:'删除整本'}))return;
                    if(await postOr('page/worldbook/books/delete',{id:book.id},'世界书已删除')){wbBookId.value=0;changeWbBook();await fetchWorldbook();}
                };
                const exportWbBook = async (format='native') => {
                    try {const r=await api.get('page/worldbook/books/export',{id:wbBookId.value,format});const url=URL.createObjectURL(new Blob([JSON.stringify(r.data,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=(wbCurrentBook.value?.name||'世界书')+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);} catch(e){showToast(e.message,true);}
                };
                const importWbBook = async event => {
                    const file=event.target.files?.[0];event.target.value='';if(!file)return;
                    try {
                        const document=JSON.parse(await file.text());if(!document.name)document.name=file.name.replace(/\.json$/i,'');
                        if(wbImportMode.value!=='new'&&!wbBookId.value)throw Error('请先选择要导入的世界书');
                        if(wbImportMode.value==='replace'&&!await askConfirm({title:'替换当前编辑的世界书',message:'当前书的全部条目会被文件中的条目替换。其他书保留。',confirmText:'替换条目'}))return;
                        const r=await api.post('page/worldbook/books/import',{document,book_id:wbImportMode.value==='new'?null:wbBookId.value,replace:wbImportMode.value==='replace'});
                        wbBookId.value=r.data.id;wbImportWarnings.value=r.data.warnings||[];await fetchWorldbook();changeWbBook();showToast(`已导入${r.data.count}条，使用前请选择全局书或当前书`);
                    }catch(e){showToast('导入失败：'+e.message,true);}
                };
                const previewWbBook = async () => {
                    try {const r=await api.post('page/worldbook/preview',{text:wbPreviewText.value,umo:filterUmo.value||null,generation:wbPreviewGeneration.value});wbPreview.value=r.data;}catch(e){showToast(e.message,true);}
                };

                const switchTab = (id) => {
                    dataList.value = [];
                    currentTab.value = id;
                    personDetail.value = null;
                    lifeDetail.value = null;

                    wbEdit.value = null;
                    wbUseProb.value = false;
                };

                const lifeStateText = ref('');                    
                const lifeOwner = ref({});                         
                const lifePlanText = ref('');                 
                const lifeList = ref([]);
                const lifeDetail = ref(null);
                const lifeCurrent = ref('');

                const fetchLives = async () => {
                    try {
                        const res = await api.get('page/lives', { limit: 100 });

                        const d = (res && res.status === 'ok' && res.data) || res || {};
                        lifeList.value = d.lives || [];
                        lifeCurrent.value = d.current || '';

                        await fetchLifeState();
                    } catch (e) {
                        lifeList.value = [];
                        showToast('读取生活线失败：' + (e?.message || e), true);
                    }
                };

                const openLife = async (lifeId) => {
                    try {
                        const res = await api.get('page/lives/detail', { life_id: lifeId });

                        const d = (res && res.status === 'ok' && res.data) || res || {};
                        if (!d.life) { showToast('读取失败', true); return; }
                        lifeDetail.value = d;
                    } catch (e) { showToast('读取失败：' + (e?.message || e), true); }
                };

                const lifeEdit = ref(null);                                

                const openLifeEdit = (l) => {
                    lifeEdit.value = {
                        life_id: l.life_id,
                        kind: l.kind || 'home',
                        title: l.title || '',
                        summary: l.summary || '',
                        with_who: l.with_who || '',
                        started_at: localISO(l.started_at),
                        ended_at: l.ended_at ? localISO(l.ended_at) : '',
                    };
                };
                const cancelLifeEdit = () => { lifeEdit.value = null; };

                const refreshLifeDetail = async () => {
                    if (!lifeDetail.value) return;
                    await openLife(lifeDetail.value.life.life_id);
                };

                const saveLifeEdit = async () => {
                    const f = lifeEdit.value;
                    if (!f) return;
                    if (await postOr('page/lives/update', f, '已保存')) {
                        lifeEdit.value = null;
                        await fetchLives();
                        if (lifeDetail.value) await refreshLifeDetail();
                    }
                };

                const deleteLife = async (l) => {
                    const ok = await askConfirm({
                        title: '删除这一段生活',
                        message: `删除「${l.title || lifeKindLabel(l.kind)}」？`,
                        confirmText: '删除'
                    });
                    if (!ok) return;
                    if (await postOr('page/lives/delete', { life_id: l.life_id },
                                     '已删除（段里的往来与内心戏一并删掉）')) {
                        lifeDetail.value = null;
                        await fetchLives();
                    }
                };

                const unlockLife = async () => {
                    const ok = await askConfirm({
                        title: '解除外出锁',
                        message: '把主线所有权还给她自己。若她正在外面，'
                               + '会顺手关掉当前段、开一段在家的。',
                        confirmText: '归家'
                    });
                    if (!ok) return;
                    if (await postOr('page/lives/unlock', {}, '已归家')) {
                        await fetchLives();
                        await fetchLifeState();
                        if (lifeDetail.value) await refreshLifeDetail();
                    }
                };

                const editLifeEntry = async (e) => {
                    const text = await askText({
                        title: '修正这条记录', value: e.text, confirmText: '保存'
                    });
                    if (text === null) return;
                    if (await postOr('page/entries/update', { id: e.id, text }, '已修正')) {
                        await refreshLifeDetail();
                    }
                };

                const deleteLifeEntry = async (e) => {
                    const ok = await askConfirm({
                        title: '删除这条记录',
                        message: '删掉这一条往来 / 内心戏？不可撤销。',
                        confirmText: '删除'
                    });
                    if (!ok) return;
                    if (await postOr('page/entries/delete', { id: e.id }, '已删除')) {
                        await refreshLifeDetail();
                    }
                };

                const editLifeTitle = async (l) => {
                    const title = await askText({
                        title: '编辑这段生活的标题',
                        hint: '时间段：' + formatTs(l.started_at) + ' → '
                              + (l.ended_at ? formatTs(l.ended_at) : '进行中'),
                        value: l.title || '',
                        placeholder: '例如：和朋友骑车去小吃摊的那晚',
                        confirmText: '保存',
                    });
                    if (title === null) return;
                    if (await postOr('page/lives/update', {
                        life_id: l.life_id, title: title, summary: l.summary || '',
                    }, '已保存')) {
                        await fetchLives();
                        if (lifeDetail.value) await openLife(l.life_id);
                    }
                };

                const lifeKindLabel = (k) => ({ home: '在家', outing: '外出', legacy: '往昔' }[k] || k);

                const drState = ref({});
                const drStorylets = ref([]);
                const drFires = ref([]);
                const drThreads = ref([]);
                const drSub = ref('storylets');           
                const drEdit = ref(null);
                const drSaving = ref(false);
                const filteredDirector = (rows) => {
                    const text = searchText.value.trim().toLocaleLowerCase();
                    return text ? rows.filter(row => JSON.stringify(row).toLocaleLowerCase().includes(text)) : rows;
                };
                const drVisibleStorylets = computed(() => filteredDirector(drStorylets.value));
                const drVisibleFires = computed(() => filteredDirector(drFires.value));
                const drVisibleThreads = computed(() => filteredDirector(drThreads.value));
                const storyletTitle = st => JSON.parse(st.meta || '{}').title || st.sid;
                const storyletText = st => {
                    const text = JSON.parse(st.narrative || '[]');
                    return Array.isArray(text) ? text.join('\n\n') : text;
                };
                const directorConditionFields = [
                    ['owner_idle_minutes','当前生活所属会话的安静分钟数'],
                    ['idle_minutes','所有会话中最长安静分钟数'],
                    ['willingness','所有会话中最高联系意愿（0～1）'],
                    ['hour','北京时间的小时数（0～23）'],
                    ['tension','剧情张力'],['beats_consumed','已推进的剧情段数'],
                    ['turn_seq','导演检查回合数'],['open_threads_n','未结束的伏笔数量']
                ];
                const conditionRules = condition => {
                    if (Object.keys(condition).some(key => !['all','any'].includes(key))) return null;
                    if ((condition.all?.length || 0) && (condition.any?.length || 0)) return null;
                    const match = condition.any?.length ? 'any' : 'all';
                    const rules = [];
                    for (const clause of condition[match] || []) {
                        if ((clause.tier ?? 1) !== 1) return null;
                        const allowed = {window:['tier','pred','var','from','to'],threshold:['tier','pred','var','op','value'],flag:['tier','pred','var','equals'],set:['tier','pred','var','in']}[clause.pred];
                        if (!allowed || Object.keys(clause).some(key=>!allowed.includes(key))) return null;
                        if (clause.pred==='set' && (!Array.isArray(clause.in)||clause.in.length!==1)) return null;
                        if (clause.pred==='window' && clause.var && clause.var!=='local_time') return null;
                        if (clause.pred==='flag' && !['asleep','has_owner','has_ending'].includes(clause.var)) return null;
                        rules.push({...clause,type:clause.pred,equals:clause.equals ?? true,value:clause.pred==='set'?clause.in[0]:clause.value});
                    }
                    return {match,rules};
                };
                const buildDirectorCondition = () => {
                    const edit = drEdit.value;
                    if (edit.conditionMode==='json') {
                        const value=JSON.parse(edit.conditionJSON);
                        if (!value || Array.isArray(value) || typeof value!=='object') throw Error('触发条件需要JSON对象');
                        return value;
                    }
                    return {[edit.match]:edit.rules.map(rule=> {
                        if (rule.type==='window') return {tier:1,pred:'window',var:'local_time',from:rule.from,to:rule.to};
                        if (rule.type==='threshold') return {tier:1,pred:'threshold',var:rule.var,op:rule.op,value:rule.value};
                        if (rule.type==='flag') return {tier:1,pred:'flag',var:rule.var,equals:rule.equals};
                        return {tier:1,pred:'set',var:rule.var,in:[rule.value]};
                    })};
                };
                const setDirectorConditionMode = mode => {
                    try {
                        const edit=drEdit.value,condition=buildDirectorCondition();
                        if (mode==='json') edit.conditionJSON=JSON.stringify(condition,null,2);
                        else {
                            const parsed=conditionRules(condition);
                            if (!parsed) throw Error('这些混合或模型判断条件需保留在自定义JSON中编辑');
                            edit.match=parsed.match;edit.rules=parsed.rules;
                        }
                        edit.conditionMode=mode;
                    } catch(error) {showToast(error.message,true);}
                };
                const addDirectorCondition = type => {
                    const defaults={window:{var:'local_time',from:'18:00',to:'23:00'},threshold:{var:'owner_idle_minutes',op:'>=',value:40},flag:{var:'asleep',equals:false},set:{var:'scene_id',value:'default'}};
                    drEdit.value.rules.push({type,...defaults[type]});
                };
                const openStorylet = st => {
                    try {
                        const meta=JSON.parse(st?.meta || '{}'),condition=JSON.parse(st?.condition || '{}');
                        const parsed=conditionRules(condition);
                        let paragraphs=JSON.parse(st?.narrative || '[]');
                        if (!Array.isArray(paragraphs)) paragraphs=[paragraphs];
                        drEdit.value={sid:st?.sid || '',title:meta.title || st?.sid || '',kind:st?.kind || 'action',enabled:!!st?.enabled,
                            paragraphs:paragraphs.length?paragraphs:[''],scope:st?.scope || 'global',priority:st?.priority ?? 0,weight:st?.weight ?? 1,
                            conditionMode:parsed?'rules':'json',conditionJSON:JSON.stringify(condition,null,2),match:parsed?.match || 'all',rules:parsed?.rules || [],
                            depends_on:JSON.parse(st?.depends_on || '[]'),k_turns:st?.k_turns ?? 0,
                            effects:JSON.stringify(JSON.parse(st?.effects || '{}'),null,2),meta,repeatable:!!meta.repeatable,
                            k_minutes:meta.k_minutes ?? 0,min_idle_minutes:meta.min_idle_minutes ?? 0,max_idle_minutes:meta.max_idle_minutes ?? 0,
                            scene_id:st?.scene_id || '',got_scene_id:st?.got_scene_id || '',ending_name:st?.ending_name || ''};
                    } catch(error) {showToast('片段数据格式需要修正：'+error.message,true);}
                };
                const saveStorylet = async () => {
                    const edit=drEdit.value;
                    try {
                        if (!edit.title.trim()) throw Error('请填写片段名称');
                        const paragraphs=edit.paragraphs.map(text=>text.trim());
                        if (paragraphs.some(text=>!text)) throw Error('请填写每段正文，或删除空段');
                        const condition=buildDirectorCondition(),effects=JSON.parse(edit.effects);
                        if (!effects || Array.isArray(effects) || typeof effects!=='object') throw Error('剧情效果需要JSON对象');
                        drSaving.value=true;
                        const meta={...edit.meta,title:edit.title.trim(),repeatable:edit.repeatable,k_minutes:edit.k_minutes,min_idle_minutes:edit.min_idle_minutes,max_idle_minutes:edit.max_idle_minutes};
                        const sid=edit.sid || 'story_'+Date.now().toString(36)+'_'+Math.random().toString(36).slice(2,9);
                        const saved=await postOr('page/director/storylets/upsert',{sid,create_only:!edit.sid,kind:edit.kind,enabled:edit.enabled,
                            narrative:paragraphs,condition,effects,meta,scope:edit.scope,priority:edit.priority,weight:edit.weight,
                            depends_on:edit.depends_on,k_turns:edit.k_turns,scene_id:edit.scene_id,got_scene_id:edit.got_scene_id,ending_name:edit.ending_name},'剧情片段已保存');
                        if (saved) {drEdit.value=null;await fetchDirector();}
                    } catch(error) {showToast(error.message,true);} finally {drSaving.value=false;}
                };

                const fetchDirector = async () => {
                    try {
                        const [rS, rL, rF, rT] = await Promise.all([
                            api.get('page/director/state'),
                            api.get('page/director/storylets'),
                            api.get('page/director/fires', { limit: 100 }),
                            api.get('page/director/threads'),
                        ]);
                        const pick = (r) => (r && r.status === 'ok' && r.data) || r || {};
                        drState.value = pick(rS);
                        drStorylets.value = pick(rL).storylets || [];
                        drFires.value = pick(rF).fires || [];
                        drThreads.value = pick(rT).threads || [];
                    } catch (e) {
                        showToast('读取导演数据失败：' + (e?.message || e), true);
                    }
                };

                const toggleStorylet = async (st) => {
                    await postOr('page/director/storylets/toggle',
                        { sid: st.sid, enabled: !st.enabled }, '已切换');
                    await fetchDirector();
                };

                const drSchemaField = async (st, field) => {
                    const cur = field === 'narrative'
                        ? (() => { try { return (JSON.parse(st.narrative) || []).join('\n'); }
                                   catch (e) { return st.narrative; } })()
                        : (() => { try { return JSON.stringify(JSON.parse(st[field] || '{}'), null, 2); }
                                   catch (e) { return st[field]; } })();
                    const v = await askText({
                        title: `编辑 ${st.sid} 的 ${field}`,
                        hint: field === 'narrative' ? '一行一段正文' : '必须是合法 JSON',
                        value: cur,
                        confirmText: '保存',
                    });
                    if (v === null || v === undefined) return;
                    let payload = v;
                    if (field === 'narrative') {
                        payload = JSON.stringify(v.split('\n').map(s => s.trim()).filter(Boolean));
                    } else {
                        try { JSON.parse(v); } catch (e) {
                            showToast(`${field} 不是合法 JSON：${e.message}`, true); return;
                        }
                    }
                    await postOr('page/director/storylets/upsert',
                        { sid: st.sid, [field]: payload }, '已保存');
                    await fetchDirector();
                };

                const lifeDur = (l) => {
                    const end = l.ended_at || (Date.now() / 1000);
                    const mins = Math.max(0, Math.round((end - l.started_at) / 60));
                    if (mins < 60) return mins + ' 分钟';
                    return (mins / 60).toFixed(1) + ' 小时';
                };

                const openPerson = async (personId) => {
                    try {
                        const res = await api.get('page/persons/detail', { person_id: personId });
                        const obj = (res && res.status === 'ok' && res.data) || res || {};
                        if (!obj.person) { showToast('读取人物档案失败', true); return; }
                        personDetail.value = {
                            person: { ...obj.person },
                            aliases: obj.aliases || [],
                            sessions: obj.sessions || [],
                            memories: obj.memories || [],
                        };
                    } catch (e) { showToast('读取失败：' + (e?.message || e), true); }
                };

                const savePerson = async () => {
                    const p = personDetail.value?.person;
                    if (!p) return;
                    await postOr('page/persons/update', {
                        person_id: p.person_id,
                        name: p.name, impression: p.impression,
                        affinity: Number(p.affinity), trust: Number(p.trust),
                    }, '已保存');
                };

                const removePerson = async (p) => {
                    const yes = await askConfirm({
                        title: '删除人物档案',
                        message: `将删除「${p.name || p.person_id}」的档案，连同他的长期认知与出现记录。`
                                 + `\n（不影响已有的会话记录。）`,
                        confirmText: '删除',
                    });
                    if (!yes) return;
                    if (await postOr('page/persons/delete', { person_id: p.person_id }, '已删除')) {
                        personDetail.value = null;
                        await fetchData();
                    }
                };

                const addPersonMemory = async () => {
                    const p = personDetail.value?.person;
                    if (!p) return;
                    const text = await askText({
                        title: '添加关于他的认知',
                        hint: '记下她应该长期记得的、关于这个人的事实。',
                        placeholder: '例如：他养了一只叫豆豆的猫',
                    });
                    if (!text) return;
                    const scope = await askConfirm({
                        title: '这条算私密还是公开？',
                        message: '「私密」= 只在私聊这类私下场合可以提起，群聊里她知道但不当众点破。\n「公开」= 任何场合都可以自然引用。',
                        confirmText: '私密', danger: false,
                    }) ? 'private' : 'public';
                    if (await postOr('page/persons/memories/add',
                        { person_id: p.person_id, text, scope }, '已添加')) {
                        await openPerson(p.person_id);
                    }
                };

                const removePersonMemory = async (id) => {
                    const p = personDetail.value?.person;
                    if (!p) return;
                    if (await postOr('page/persons/memories/delete', { id }, '已删除')) {
                        await openPerson(p.person_id);
                    }
                };

                const fetchL3 = async () => {
                    const res = await api.get('page/l3/state', { umo: filterUmo.value });
                    const obj = (res && res.status === 'ok' && res.data) || res || {};
                    l3.value = obj;

                    await fetchLifeState();
                };

                const fetchLifeState = async () => {
                    try {
                        const res = await api.get('page/life/state');

                        const d = (res && res.status === 'ok' && res.data) || res || {};
                        lifeStateText.value = d.state || '';
                        lifePlanText.value = d.plans || '';

                        lifeOwner.value = d.owner || {};
                    } catch (e) {                    }
                };

                const postOr = async (path, body, okMsg) => {
                    try {
                        await api.post(path, body);
                        if (okMsg) showToast(okMsg);
                        return true;
                    } catch (e) {
                        showToast('操作失败：' + (e?.message || e), true);
                        return false;
                    }
                };

                const localISO = (ts) => {
                    const d = ts instanceof Date ? ts : new Date(ts * 1000);
                    const p = (n) => String(n).padStart(2, '0');
                    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
                };

                const saveWillingness = async () => {
                    await postOr('page/l3/update', {umo: filterUmo.value, willingness: l3.value.willingness}, '主动联系意愿已保存');
                };
                const saveL3 = async () => {

                    const txt = (lifeStateText.value || '').trim();
                    if (!txt) return showToast("生活主线不能为空", true);
                    const ok = await postOr('page/life/state/set', { state: txt }, "生活主线已更新");
                    if (ok) refresh();
                };

                const resetClock = async () => {
                    const ok = await askConfirm({
                        title: "重置叙事时钟",
                        message: "确定把时钟对齐到此刻吗？这将消除已有的时间流逝记忆。",
                        confirmText: "重置"
                    });
                    if (!ok) return;
                    if (await postOr('page/l3/reset_clock', { umo: filterUmo.value })) showToast("时钟已对齐");
                };

                const clearVolatile = async () => {
                    const ok = await askConfirm({
                        title: "清空待合并变化",
                        message: "确定丢弃这些未合流的状态碎片吗？",
                        confirmText: "清空"
                    });
                    if (!ok) return;
                    await postOr('page/l3/clear_volatile', { umo: filterUmo.value }, "缓冲区已清空");
                    fetchL3();
                };

                const addMemory = async () => {
                    const text = await askText({
                        title: "录入核心认知",
                        hint: "她应该长期记住关于这个人的什么事实？",
                        placeholder: "例如：他不喜欢吃芹菜",
                        confirmText: "记下"
                    });
                    if (text && text.trim()) {
                        await postOr('page/memories/add', { umo: filterUmo.value, text: text.trim() }, "已记下");
                        fetchData();
                    }
                };

                const editMemory = async (m) => {
                    const text = await askText({ title: "编辑认知", value: m.text, confirmText: "保存" });
                    if (text !== null && text !== m.text) {
                        await postOr('page/memories/update', { id: m.id, text, importance: m.importance }, "已保存");
                        fetchData();
                    }
                };

                const updateMemoryImportance = async (m, val) => {
                    await postOr('page/memories/update', { id: m.id, text: m.text, importance: parseFloat(val) });
                    fetchData();
                };

                const deleteMemory = async (id) => {
                    const ok = await askConfirm({
                        title: "删除认知",
                        message: "彻底让这段认知消失吗？此操作不可撤销。",
                        confirmText: "遗忘"
                    });
                    if (!ok) return;
                    await postOr('page/memories/delete', { id }, "已遗忘");
                    fetchData();
                };

                const toggleSources = async (m) => {
                    const mid = m.id;
                    sourceOpen.value = { ...sourceOpen.value, [mid]: !sourceOpen.value[mid] };
                    if (!sourceOpen.value[mid] || sourceCache.value[mid]) return;
                    try {
                        const res = await api.get('page/entries/by_ids', {
                            ids: String(m.source_entry_ids || '').split(',').filter(Boolean).join(',')
                        });
                        const d = (res && res.status === 'ok' && res.data) || res || {};
                        sourceCache.value = { ...sourceCache.value, [mid]: d.items || [] };
                    } catch (e) {
                        sourceCache.value = { ...sourceCache.value, [mid]: [] };
                        showToast('来源读取失败：' + (e?.message || e), true);
                    }
                };

                const sourceCount = (m) => String(m.source_entry_ids || '').split(',').filter(Boolean).length;

                const addIntent = async () => {
                    const summary = await askText({
                        title: "手动派发任务",
                        hint: "给她的未来安排一件事。",
                        placeholder: "任务摘要",
                        confirmText: "下一步"
                    });
                    if (!summary || !summary.trim()) return;
                    const due = await askText({
                        title: "执行时间",
                        hint: "格式 YYYY-MM-DDTHH:mm:ss",
                        value: localISO(new Date(Date.now() + 3600000)),
                        confirmText: "派发"
                    });
                    if (!due) return;
                    await postOr('page/intents/add', { umo: filterUmo.value, summary: summary.trim(), not_before: due }, "已派发");
                    fetchData();
                };

                const editIntent = async (i) => {
                    const summary = await askText({ title: "修正任务摘要", value: i.summary, confirmText: "下一步" });
                    if (summary === null) return;
                    const due = await askText({
                        title: "修正计划时间",
                        hint: "格式 YYYY-MM-DDTHH:mm:ss",
                        value: localISO(i.not_before),
                        confirmText: "保存"
                    });
                    if (due === null) return;
                    await postOr('page/intents/update', { id: i.id, summary, not_before: due }, "已修正");
                    fetchData();
                };

                const editPending = async (p) => {
                    const content = await askText({ title: "修正投递内容", value: p.content, confirmText: "下一步" });
                    if (content === null) return;
                    const due = await askText({
                        title: "修正投递时间",
                        hint: "格式 YYYY-MM-DDTHH:mm:ss",
                        value: localISO(p.due_ts),
                        confirmText: "保存"
                    });
                    if (due === null) return;
                    await postOr('page/pending/update', { id: p.id, content, due_at: due }, "已修正");
                    fetchData();
                };

                const editEntry = async (e) => {
                    const text = await askText({ title: "修正日志文本", value: e.text, confirmText: "保存" });
                    if (text !== null) {
                        await postOr('page/entries/update', { id: e.id, text }, "已修正");
                        fetchData();
                    }
                };

                const deleteEntry = async (id) => {
                    const ok = await askConfirm({
                        title: "删除轨迹",
                        message: "彻底删除这条轨迹吗？此操作不可撤销。",
                        confirmText: "删除"
                    });
                    if (!ok) return;
                    await postOr('page/entries/delete', { id }, "已删除");
                    fetchData();
                };

                const cancelIntent = async (id) => {
                    const ok = await askConfirm({ title: "取消任务承诺", message: "取消该任务承诺？", confirmText: "取消该任务" });
                    if (!ok) return;
                    await postOr('page/intents/cancel', { id }, "已取消");
                    fetchData();
                };

                const retryPending = async (id) => {
                    const ok = await askConfirm({ title: "恢复待发", message: "先核对聊天记录：这条消息可能已送达。确认需要重新发送？", confirmText: "恢复待发" });
                    if (!ok) return;
                    await postOr('page/pending/update', { id, retry: true }, "已恢复待发");
                    fetchData();
                };
                const cancelPending = async (id) => {
                    const ok = await askConfirm({ title: "作废消息", message: "作废这条待投递消息？", confirmText: "作废" });
                    if (!ok) return;
                    await postOr('page/pending/cancel', { id }, "已作废");
                    fetchData();
                };

                const formatTs = (ts) => ts ? new Date(ts * 1000).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '-';
                const formatPlanTime = (iso) => { try { return iso.split('T')[1].substring(0, 5); } catch(e) { return '?'; } };

                const waitBridge = () => {
                    let attempts = 0;
                    const check = setInterval(() => {
                        attempts++;
                        if (window.RoleModulePage) {
                            clearInterval(check);
                            bridge = window.RoleModulePage;
                            statusMsg.value = '正在读取数据…';
                            bridge.ready().then(() => { initialized.value = true; refresh(); }).catch(e => {fatalError.value = e.message;});
                        } else if (attempts > 60) {
                            clearInterval(check);
                            fatalError.value = '数据接口连接超时，请刷新页面或检查平台运行日志。';
                        }
                    }, 100);
                };

                const selection=ref(new RecordSelection());
                const manageBooks=ref(false);
                const selectionResource=computed(()=>{
                    if(personDetail.value)return 'spectrum.person_memories';
                    if(lifeDetail.value)return 'spectrum.entries';
                    if(currentTab.value==='worldbook')return manageBooks.value?'spectrum.worldbook_books':'spectrum.worldbook';
                    if(currentTab.value==='tools')return 'spectrum.tool_notes';
                    if(currentTab.value==='director')return ({storylets:'spectrum.storylets',fires:'spectrum.director_fires',threads:'spectrum.drama_threads'})[drSub.value]||'';
                    return 'spectrum.'+currentTab.value;
                });
                const selectionKey=row=>String(row.id??row.life_id??row.person_id??row.sid??row.tid??row.name);
                const selectionRows=computed(()=>{
                    if(personDetail.value)return personDetail.value.memories||[];
                    if(lifeDetail.value)return lifeDetail.value.entries||[];
                    if(currentTab.value==='lives')return filteredLives.value;
                    if(currentTab.value==='worldbook')return manageBooks.value?wbBooks.value:wbCards.value;
                    if(currentTab.value==='tools')return filteredTools.value.filter(row=>row.note);
                    if(currentTab.value==='director')return ({storylets:drVisibleStorylets.value,fires:drVisibleFires.value,threads:drVisibleThreads.value})[drSub.value]||[];
                    return filteredDataList.value;
                });
                const selectionFilters=()=>({umo:globalTab.value?'':filterUmo.value,
                    history:showHistory.value,search:searchText.value,
                    life_id:lifeDetail.value?.life?.life_id||'',person_id:personDetail.value?.person?.person_id||'',
                    book_id:currentTab.value==='worldbook'&&!manageBooks.value?wbBookId.value:''});
                const selectPage=()=>selection.value.add(selectionRows.value.filter(row=>row.status!=='sending').map(selectionKey));
                const selectAll=async()=>{
                    const resource=selectionResource.value,filters=selectionFilters(),scope=JSON.stringify([resource,filters]),revision=selection.value.revision;
                    selection.value.busy=true;
                    try{const data=await recordRequest('query',{resource,filters,select_all:true});
                        if(revision===selection.value.revision&&scope===JSON.stringify([selectionResource.value,selectionFilters()]))selection.value.replace(data.ids);
                    }catch(error){showToast(error.message,true);}finally{selection.value.busy=false;}
                };
                const deleteSelected=async()=>{
                    const resource=selectionResource.value,ids=[...selection.value.ids];
                    if(!ids.length)return;
                    selection.value.busy=true;
                    try{
                        const info=await recordRequest('query',{resource,limit:1,filters:selectionFilters()});
                        if(!await askConfirm({title:'删除所选'+info.title,message:`确认删除选中的 ${ids.length} 条？${info.impact}`,confirmText:'确认删除'}))return;
                        const result=await recordRequest('delete',{resource,ids,confirmed:true});
                        selection.value.exit();sourceCache.value={};sourceOpen.value={};
                        showToast(`已删除 ${result.deleted} 条${result.missing?'，'+result.missing+' 条已不存在或不属于当前范围':''}`);
                        if(personDetail.value)await openPerson(personDetail.value.person.person_id);
                        else if(lifeDetail.value)await openLife(lifeDetail.value.life.life_id);
                        else await refresh();
                    }catch(error){showToast(error.message,true);}finally{selection.value.busy=false;}
                };
                watch([selectionResource,filterUmo,showHistory,searchText,wbBookId,
                    ()=>personDetail.value?.person?.person_id||'',()=>lifeDetail.value?.life?.life_id||''],()=>selection.value.exit());

                onMounted(waitBridge);

                watch([currentTab, filterUmo, showHistory], () => {
                    personDetail.value = null; pageNumber.value = 1; searchText.value = '';
                    fetchData();
                });
                watch(filterUmo, () => {if (filterUmo.value) fetchL3().catch(e => showToast(e.message, true));});

                return {
                    selection,manageBooks,selectionResource,selectionKey,selectPage,selectAll,deleteSelected,
                    initialized, statusMsg, fatalError, overview, umos, currentTab, filterUmo, showHistory,
                    dataList, l3, stats, tabs, activeTab, globalTab, pagedTab, searchText, loadingData, loadError, filteredDataList, filteredLives, filteredTools, pageNumber, pageSize, changePage, statusLabel, kindLabel, trapDialog, saveWillingness,
                    lifeList, lifeDetail, lifeCurrent, openLife, lifeKindLabel, lifeDur,
                    lifeStateText, lifePlanText, lifeOwner, fetchLifeState, editLifeTitle,
                    fetchLives,
                    lifeEdit, openLifeEdit, cancelLifeEdit, saveLifeEdit,
                    deleteLife, unlockLife, editLifeEntry, deleteLifeEntry,
                    dialog, toast, toastIsError, confirmDialog, closeAndCancel,
                    refresh, formatTs, formatPlanTime, saveL3, resetClock, clearVolatile,
                    addMemory, editMemory, updateMemoryImportance, deleteMemory,
                    sourceOpen, sourceCache, toggleSources, sourceCount,
                    addIntent, editIntent, cancelIntent, editPending, cancelPending, retryPending,
                    editEntry, deleteEntry,
                    personDetail, openPerson, savePerson, removePerson,
                    addPersonMemory, removePersonMemory, switchTab,
                    drState, drStorylets, drFires, drThreads, drSub,
                    fetchDirector, toggleStorylet, drSchemaField,
                    drEdit,drSaving,drVisibleStorylets,drVisibleFires,drVisibleThreads,storyletTitle,storyletText,
                    directorConditionFields,openStorylet,saveStorylet,setDirectorConditionMode,addDirectorCondition,
                    toolsList, toolsSource, toolsOrphans, fetchTools, editToolNote,
                    wbList, wbConfig, wbEdit, wbCards, wbIsOpen, wbState, wbStateLabel,
                    wbUseProb, wbEditTokens, toggleWbCard,
                    wbBooks,wbBookId,wbSelection,wbImportMode,wbImportWarnings,wbCurrentBook,wbPositions,
                    wbPreviewText,wbPreviewGeneration,wbPreview,changeWbBook,saveWbSelection,saveWbBook,duplicateWbBook,deleteWbBook,exportWbBook,importWbBook,previewWbBook,
                    fetchWorldbook, addWorldbook, cancelWorldbook,
                    saveWorldbook, deleteWorldbook, toggleWorldbook
                };
            }
        }).mount('#app');
