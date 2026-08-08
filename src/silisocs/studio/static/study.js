/* The study composer (Definition tab) and the study board's live refresh.
 *
 * Moved verbatim out of study.html; only the server-supplied values (study id,
 * definition, reusable run choices, board stream) changed, and they now arrive
 * through the page's JSON data island. Functions stay top-level declarations
 * because the template's `onclick="launchStudy()"` handlers resolve them as
 * globals. */

const studyPage = studioPageData();
const studyId = studyPage.id;
const runChoices = studyPage.runChoices || [];
let studyDefinition = studyPage.definition || {};

/* ---- dirty state --------------------------------------------------------- */
let studyDirty=false;
function markStudyDirty(){studyDirty=true;const state=document.getElementById('study-save-state');if(state){state.textContent='Unsaved changes';state.classList.add('dirty')}}
function markStudySaved(){studyDirty=false;const state=document.getElementById('study-save-state');if(state){state.textContent='Saved';state.classList.remove('dirty')}}
window.addEventListener('beforeunload',event=>{if(studyDirty){event.preventDefault();event.returnValue=''}});

/* ---- form <-> definition <-> YAML mirror --------------------------------- */
const yamlEditor=document.getElementById('study-yaml');
function studyFieldValue(input){if(input.dataset.studyScenarios!==undefined){const detected=[...input.querySelectorAll('input:checked')].map(item=>item.value),custom=input.parentElement.querySelector('[data-study-custom-scenarios]').value.split('\n').map(value=>value.trim()).filter(Boolean);return [...new Set([...detected,...custom])]}if(input.dataset.list)return input.value.split('\n').map(value=>value.trim()).filter(Boolean);if(input.dataset.yaml){try{return JSON.parse(input.value||'{}')}catch{notify('Override diff must be valid JSON or edited in the YAML mirror.','danger');throw new Error('invalid value')}}if(input.type==='number')return Number(input.value);return input.value}
function studyValueAt(definition,path){return path.split('.').reduce((value,key)=>value&&typeof value==='object'?value[key]:undefined,definition)}
function displayStudyField(input,value){if(input.dataset.studyScenarios!==undefined){const selected=new Set(value||[]);input.querySelectorAll('input').forEach(item=>item.checked=selected.has(item.value));return}if(input.dataset.list){input.value=Array.isArray(value)?value.join('\n'):'';return}if(input.dataset.yaml){input.value=JSON.stringify(value??{},null,2);return}input.value=value??''}
function hydrateStudy(definition){studyDefinition=definition;document.querySelectorAll('[data-study-field]').forEach(input=>displayStudyField(input,studyValueAt(definition,input.dataset.studyField)))}
async function composeStudy(updates={}){const response=await fetch(`/api/studies/${studyId}/compose`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({yaml:yamlEditor.value,updates})});if(!response.ok){notify(await response.text(),'danger');return false}const data=await response.json();yamlEditor.value=data.yaml;hydrateStudy(data.definition);markStudyDirty();return data}
document.getElementById('study-form')?.addEventListener('change',async event=>{const input=event.target.closest('[data-study-field]');if(!input)return;const updates={[input.dataset.studyField]:studyFieldValue(input)};if(input.dataset.studyScenarios!==undefined&&event.target.matches('input[type="checkbox"]')&&event.target.checked){input.querySelectorAll('input[type="checkbox"]').forEach(box=>{if(box!==event.target&&box.dataset.scenarioSource!==event.target.dataset.scenarioSource)box.checked=false});updates[input.dataset.studyField]=studyFieldValue(input);updates['study.run_defaults.config_path']=event.target.dataset.configPattern;updates['study.run_defaults.working_directory']=event.target.dataset.projectRoot}await composeStudy(updates)});
document.querySelector('[data-study-custom-scenarios]')?.addEventListener('change',async event=>{const input=event.target.closest('.form-field').querySelector('[data-study-scenarios]');await composeStudy({[input.dataset.studyField]:studyFieldValue(input)})});
yamlEditor?.addEventListener('change',()=>composeStudy({}));

/* ---- structure editing (hypotheses, conditions, evaluators, reuse) -------- */
let pendingStudyEntity={kind:'Hypothesis',hypothesis:null};
function openStudyEntityDialog(kind,hypothesis=null){pendingStudyEntity={kind,hypothesis};document.getElementById('study-entity-title').textContent=`Add ${kind.toLowerCase()}`;const input=document.getElementById('study-entity-id');input.value='';document.getElementById('study-entity-dialog').showModal();input.focus()}
async function createStudyEntity(event){event.preventDefault();const id=document.getElementById('study-entity-id').value.trim();if(!/^[A-Za-z0-9_-]+$/.test(id))return;const updates=pendingStudyEntity.kind==='Hypothesis'?{[`hypotheses.${id}`]:{statement:'',independent_variable:'',prediction:'',status:'planning',conditions:{baseline:{overrides:{}}}}}:{[`hypotheses.${pendingStudyEntity.hypothesis}.conditions.${id}`]:{overrides:{}}};const data=await composeStudy(updates);if(data){document.getElementById('study-entity-dialog').close();await saveStudy();location.reload()}}
async function addEvaluation(){const preset=document.getElementById('evaluation-preset').value;if(!preset)return;const evaluations=Array.isArray(studyDefinition.evaluations)?[...studyDefinition.evaluations]:[];if(evaluations.some(item=>item?.preset===preset)){notify('That evaluator is already selected.','danger');return}evaluations.push({id:preset.split('.').pop(),preset});if(await composeStudy({evaluations})){await saveStudy();location.reload()}}
async function addExistingRun(hypothesis,condition){const picker=[...document.querySelectorAll('[data-reuse-hypothesis]')].find(el=>el.dataset.reuseHypothesis===hypothesis&&el.dataset.reuseCondition===condition),run=runChoices[Number(picker?.value)];if(!run)return;const path=`hypotheses.${hypothesis}.conditions.${condition}`,existing=studyValueAt(studyDefinition,`${path}.reuse.runs`),runs=Array.isArray(existing)?[...existing]:[];if(!runs.some(item=>item.source===run.path))runs.push({scenario:run.scenario,seed:run.seed,source:run.path});const data=await composeStudy({[`${path}.execution.mode`]:'reuse_existing',[`${path}.reuse.runs`]:runs});if(data){await saveStudy();location.reload()}}

/* ---- save and launch ----------------------------------------------------- */
async function launchStudy(){if(yamlEditor&&!await saveStudy())return;const response=await fetch(`/api/studies/${studyId}/launch`,{method:'POST',headers:{'content-type':'application/json'},body:'{}'});if(response.ok){const job=await response.json();location.href='/live?job='+job.id}else notify(await response.text(),'danger')}
async function saveStudy(){if(!yamlEditor)return true;const response=await fetch(`/api/studies/${studyId}`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({yaml:yamlEditor.value})});if(response.ok)markStudySaved();notify(response.ok?'Saved':await response.text(),response.ok?'success':'danger');return response.ok}

/* ---- live board ---------------------------------------------------------- */
// Every board panel refreshes through the shared refreshPanel (panels.js),
// which resolves the generic study-panel endpoint from data-study-id — no
// panel name, column list, or bespoke fetch path is wired here.
if (studyPage.boardStream) {
  const refreshStudyBoard = async () => {
    for (const section of document.querySelectorAll('.panel-grid [data-panel]')) await refreshPanel(section);
  };
  const studyEvents = new EventSource(studyPage.boardStream);
  studyEvents.addEventListener('artifact_grown', refreshStudyBoard);
  studyEvents.addEventListener('status_changed', refreshStudyBoard);
  studyEvents.addEventListener('done', () => {
    refreshStudyBoard();
    studyEvents.close();
  });
}
