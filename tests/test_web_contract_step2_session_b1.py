from pathlib import Path
import unittest
from PySide6.QtCore import QCoreApplication
from PySide6.QtQml import QJSEngine


class Step2SessionB1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = (Path(__file__).parents[1] / "src" / "icp_renov_contracts" / "ui_web" / "contract-workspace.js").read_text(encoding="utf-8")

    def engine(self, include_mutation=False, include_period=False, include_actions=False, include_review=False, include_preview=False, include_pricing_preview=False):
        QCoreApplication.instance() or QCoreApplication([])
        engine = QJSEngine()
        harness = """var contractWorkspaceState={contract:{id:'contract-A',editable:true}};var step2BlockUiState={};var managedStep2BlockIds=['SERVICE','PERIOD','INTERVENTION','PRICING','RENEWAL','SPECIAL_TERMS'];var periodPreviewRequestId=0,pricingPreviewRequestId=0,step2PreviewRenderEpoch=0,periodPreviewCallbacks=[],pricingPreviewCallbacks=[];var renderCount=0,globalFeedbackCount=0,updatePeriodCalls=0,updatePricingCalls=0,previewPeriodCalls=0,previewPricingCalls=0,frameworkCalls=0,templateCalls=0,reviewCalls=0;var frameworkResult={ok:true},templateResult={ok:true},reviewResult={ok:true};function renderContractWorkspace(){renderCount++;}function closeContractDrawer(){}function showContractSaveFeedback(){globalFeedbackCount++;}var serviceControls=[],templateControl={disabled:false},serviceActions=null,serviceSection=null,periodNodes={},pricingNodes={},pricingTargets=[];function installServiceDom(){serviceControls=[{disabled:false},{disabled:false},{disabled:false},{disabled:false},{disabled:false}];serviceActions={innerHTML:'<button>Enregistrer les prestations</button>',removed:false,querySelector:function(){return null;},insertAdjacentHTML:function(_,value){this.innerHTML+=value;},remove:function(){this.removed=true;this.innerHTML='';}};serviceSection={dataset:{},classList:{add:function(){},remove:function(){}},querySelectorAll:function(){return serviceControls;},querySelector:function(){return serviceActions;}};}function previewNode(value){return {value:value||'',textContent:'',dataset:{},listeners:{},addEventListener:function(name,fn){this.listeners[name]=fn;}};}function installPeriodPreviewDom(){periodNodes={'#contract-start-date':previewNode('2026-01-01'),'#contract-duration-mode':previewNode('STANDARD'),'#contract-duration-months':previewNode('12'),'#contract-end-date':previewNode('2027-01-01'),'#period-preview-status':previewNode(''),'#period-dependent-fields':previewNode('')};periodNodes['#period-preview-status'].textContent='Calculée automatiquement';}function installPricingPreviewDom(){pricingTargets=[previewNode(''),previewNode('')];pricingTargets[0].textContent='20,00 €';pricingTargets[1].textContent='120,00 €';pricingNodes={'#contract-annual-ht':previewNode('100'),'#contract-vat-rate':previewNode('20'),'#contract-vat-amount':pricingTargets[0],'#contract-annual-ttc':pricingTargets[1]};}var fakeSaveState={textContent:'',classList:{add:function(){},remove:function(){}}};var document={querySelector:function(selector){if(selector==='.service-offer-section')return serviceSection;if(periodNodes[selector])return periodNodes[selector];if(pricingNodes[selector])return pricingNodes[selector];return fakeSaveState;},querySelectorAll:function(selector){return selector==='.pricing-section .calculated-money strong'?pricingTargets:[];},createElement:function(){return {className:'',textContent:''};}};var bridge={updateContractPeriod:function(){updatePeriodCalls++;},updateContractPricing:function(){updatePricingCalls++;},previewContractPeriod:function(id,payload,done){previewPeriodCalls++;periodPreviewCallbacks.push({id:id,payload:payload,done:done});},previewContractPricing:function(id,payload,done){previewPricingCalls++;pricingPreviewCallbacks.push({id:id,payload:payload,done:done});},updateContractFramework:function(_,__,done){frameworkCalls++;done(frameworkResult);},selectContractTemplateVersion:function(_,__,done){templateCalls++;done(templateResult);},openContractReviewBlock:function(_,__,done){reviewCalls++;done(reviewResult);}};function esc(value){return String(value==null?'':value);}"""
        engine.evaluate(harness)
        def piece(start, end): return self.js[self.js.index(start):self.js.index(end, self.js.index(start))]
        def function_piece(start):
            offset = self.js.index(start); end = self.js.index("\n}\n\nfunction", offset) + 2
            return self.js[offset:end]
        source = piece("function step2BlockState", "function closeContractDrawer")
        if include_mutation: source += function_piece("function contractMutation")
        if include_period: source += piece("function disableManagedStep2Controls", "function renderContractB2")
        if include_actions:
            source += function_piece("function updateContractRegime")
            source += function_piece("function selectContractTemplateVersion")
        if include_review: source += function_piece("function openReviewBlock")
        if include_preview: source += piece("function periodDependentMarkup", "function saveContractPeriod")
        if include_pricing_preview: source += piece("function pricingPreviewAllowed", "function saveContractPeriod")
        result = engine.evaluate(source); self.assertFalse(result.isError(), result.toString())
        return engine

    def test_executable_period_state_transitions_and_contract_isolation(self):
        e = self.engine(True)
        self.assertTrue(e.evaluate("step2BlockState('PERIOD').mode==='EDITING'&&!step2BlockState('PERIOD').hasSaved&&step2BlockState('PERIOD').feedback==='' ").toBool())
        e.evaluate("step2BlockState('PERIOD').mode='SAVED';contractWorkspaceState.contract.id='contract-B';")
        self.assertTrue(e.evaluate("step2BlockState('PERIOD').mode==='EDITING'&&!step2BlockState('PERIOD').hasSaved").toBool())
        e.evaluate("contractWorkspaceState.contract.id='contract-A';contractMutation(function(done){done({ok:true,message:'Période enregistrée.'});},true,'Période enregistrée.',{step2BlockId:'PERIOD'});")
        self.assertTrue(e.evaluate("step2BlockState('PERIOD').mode==='SAVED'&&step2BlockState('PERIOD').hasSaved&&step2BlockState('PERIOD').feedback==='Période enregistrée.'&&renderCount===1&&globalFeedbackCount===0").toBool())
        e.evaluate("editStep2Block('PERIOD');")
        self.assertTrue(e.evaluate("step2BlockState('PERIOD').mode==='EDITING'&&step2BlockState('PERIOD').hasSaved&&step2BlockState('PERIOD').feedback==='' ").toBool())
        e.evaluate("renderCount=0;cancelStep2Block('PERIOD');")
        self.assertTrue(e.evaluate("step2BlockState('PERIOD').mode==='SAVED'&&renderCount===1&&updatePeriodCalls===0&&previewPeriodCalls===0").toBool())
        e.evaluate("contractWorkspaceState.contract.id='contract-B';contractMutation(function(done){done({ok:false,message:'Erreur'});},true,'Période enregistrée.',{step2BlockId:'PERIOD'});")
        self.assertTrue(e.evaluate("step2BlockState('PERIOD').mode==='EDITING'&&!step2BlockState('PERIOD').hasSaved&&step2BlockState('PERIOD').feedback===''&&renderCount===1").toBool())

    def test_executable_period_markup_actions_and_authoritative_cancel_value(self):
        e = self.engine(False, True)
        markup = '<article class="contract-section conditions-b2-section period-section"><input value="12"><div class="conditions-actions"><button class="primary">Enregistrer la période</button></div></article>'
        e.evaluate("var markup=" + repr(markup) + ";")
        fresh = e.evaluate("presentPeriodBlock(markup)").toString()
        self.assertIn("Enregistrer la période", fresh); self.assertNotIn("Modifier", fresh); self.assertNotIn("Annuler", fresh)
        e.evaluate("var state=step2BlockState('PERIOD');state.mode='SAVED';state.hasSaved=true;state.feedback='Période enregistrée.';")
        saved = e.evaluate("presentPeriodBlock(markup)").toString()
        self.assertIn("Modifier", saved); self.assertIn("Période enregistrée.", saved); self.assertNotIn("Enregistrer la période", saved)
        e.evaluate("editStep2Block('PERIOD');")
        editing = e.evaluate("presentPeriodBlock(markup)").toString()
        self.assertIn("Enregistrer la période", editing); self.assertIn("Annuler", editing); self.assertNotIn("Modifier", editing)
        e.evaluate("cancelStep2Block('PERIOD');")
        self.assertIn('value="12"', e.evaluate("presentPeriodBlock(markup)").toString())

    def test_global_read_only_contract_overrides_saved_period_actions(self):
        e = self.engine(False, True)
        e.evaluate("contractWorkspaceState.contract.editable=false;var state=step2BlockState('PERIOD');state.mode='SAVED';state.hasSaved=true;state.feedback='Période enregistrée.';")
        markup = '<article class="contract-section conditions-b2-section period-section"><input value="12"><div class="conditions-actions"><button>Enregistrer la période</button></div></article>'
        e.evaluate("var readonlyMarkup=" + repr(markup) + ";")
        rendered = e.evaluate("presentPeriodBlock(readonlyMarkup)").toString()
        for forbidden in ("Modifier", "Enregistrer la période", "Annuler", "Période enregistrée."):
            self.assertNotIn(forbidden, rendered)
        e.evaluate("editStep2Block('PERIOD');")
        self.assertTrue(e.evaluate("step2BlockState('PERIOD').mode==='SAVED'").toBool())

    def test_global_read_only_disables_every_managed_control_for_all_session_states(self):
        e = self.engine(False, True)
        dependent_controls = {
            "PERIOD": ('period-duration', 'period-mode', 'period-city'),
            "INTERVENTION": ('missed-appointment-fee', 'travel-included', 'additional-exclusions'),
            "PRICING": ('contract-payment-due-days', 'contract-payment-term', 'contract-payment-custom'),
            "RENEWAL": ('contract-renewal-period', 'contract-renewal-mode', 'contract-non-renewal-notice-days'),
            "SPECIAL_TERMS": ('special-terms-input', 'special-terms-select', 'contract-special-terms'),
        }
        e.evaluate("contractWorkspaceState.contract.editable=false;")
        for block, (input_id, select_id, textarea_id) in dependent_controls.items():
            markup = f'<article class="contract-section test-section"><input id="{input_id}"><select id="{select_id}"><option>Choix</option></select><textarea id="{textarea_id}">Texte</textarea><div class="conditions-actions"><button>Enregistrer</button></div></article>'
            e.evaluate("var readonlyBlockMarkup=" + repr(markup) + ";")
            for mode, has_saved, feedback in (("EDITING", False, ""), ("SAVED", True, f"{block} enregistré")):
                e.evaluate(f"var current=step2BlockState('{block}');current.mode='{mode}';current.hasSaved={str(has_saved).lower()};current.feedback={feedback!r};")
                rendered = e.evaluate(f"presentManagedStep2Block('{block}', readonlyBlockMarkup)").toString()
                for control_id in (input_id, select_id, textarea_id):
                    self.assertRegex(rendered, rf'id="{control_id}"[^>]*\bdisabled\b')
                for forbidden in ("Modifier", "Enregistrer", "Annuler", feedback):
                    if forbidden:
                        self.assertNotIn(forbidden, rendered)

    def test_period_preview_is_authoritative_dom_local_and_truthful_while_pending(self):
        e = self.engine(include_preview=True)
        e.evaluate("installPeriodPreviewDom();wirePeriodPreview();periodNodes['#contract-start-date'].listeners.change();")
        self.assertTrue(e.evaluate("previewPeriodCalls===1&&periodPreviewCallbacks[0].payload.initial_duration_months===12&&periodNodes['#contract-end-date'].value===''&&periodNodes['#period-preview-status'].textContent==='À calculer'&&renderCount===0&&updatePeriodCalls===0").toBool())
        e.evaluate("periodPreviewCallbacks[0].done({ok:true,resolved_end_date:'2027-01-01'});")
        self.assertTrue(e.evaluate("periodNodes['#contract-end-date'].value==='2027-01-01'&&periodNodes['#period-preview-status'].textContent==='Calculée automatiquement'&&renderCount===0").toBool())
        e.evaluate("periodNodes['#contract-duration-months'].value='';requestContractPeriodPreview();periodPreviewCallbacks[1].done({ok:false,message:'À calculer'});")
        self.assertTrue(e.evaluate("periodNodes['#contract-end-date'].value===''&&periodNodes['#period-preview-status'].textContent==='À calculer'&&renderCount===0&&updatePeriodCalls===0").toBool())

    def test_period_preview_rejects_out_of_order_mode_and_render_stale_callbacks(self):
        e = self.engine(include_preview=True)
        e.evaluate("installPeriodPreviewDom();requestContractPeriodPreview();periodNodes['#contract-duration-months'].value='24';requestContractPeriodPreview();periodPreviewCallbacks[1].done({ok:true,resolved_end_date:'2028-01-01'});periodPreviewCallbacks[0].done({ok:true,resolved_end_date:'2027-01-01'});")
        self.assertTrue(e.evaluate("periodNodes['#contract-end-date'].value==='2028-01-01'").toBool())
        e.evaluate("periodNodes['#contract-duration-months'].value='36';requestContractPeriodPreview();periodNodes['#contract-duration-mode'].value='CUSTOM';periodNodes['#contract-end-date']=previewNode('2030-01-01');periodPreviewCallbacks[2].done({ok:true,resolved_end_date:'2029-01-01'});")
        self.assertTrue(e.evaluate("periodNodes['#contract-end-date'].value==='2030-01-01'").toBool())
        e.evaluate("periodNodes['#contract-duration-mode'].value='STANDARD';periodNodes['#contract-duration-months'].value='48';requestContractPeriodPreview();step2PreviewRenderEpoch+=1;periodPreviewCallbacks[3].done({ok:true,resolved_end_date:'2030-01-01'});")
        self.assertTrue(e.evaluate("periodNodes['#contract-end-date'].value===''&&periodNodes['#period-preview-status'].textContent==='À calculer'").toBool())

    def test_period_preview_respects_session_and_global_authority_without_js_calculation(self):
        e = self.engine(include_preview=True)
        e.evaluate("installPeriodPreviewDom();var period=step2BlockState('PERIOD');period.mode='SAVED';requestContractPeriodPreview();contractWorkspaceState.contract.editable=false;period.mode='EDITING';requestContractPeriodPreview();")
        self.assertTrue(e.evaluate("previewPeriodCalls===0").toBool())
        start = self.js.index("function requestContractPeriodPreview")
        end = self.js.index("function wirePeriodPreview", start)
        source = self.js[start:end]
        self.assertIn("bridge.previewContractPeriod", source)
        self.assertNotIn("updateContractPeriod", source)
        self.assertNotIn("renderContractWorkspace", source)
        self.assertNotIn("bridge.refresh", source)
        self.assertNotIn("new Date", source)

    def test_pricing_preview_is_authoritative_dom_local_and_truthful_while_pending(self):
        e = self.engine(include_pricing_preview=True)
        e.evaluate("installPricingPreviewDom();wirePricingPreview();pricingNodes['#contract-annual-ht'].listeners.input();")
        self.assertTrue(e.evaluate("pricingTargets[0].id==='contract-vat-amount'&&pricingTargets[1].id==='contract-annual-ttc'&&previewPricingCalls===1&&pricingPreviewCallbacks[0].payload.annual_ht==='100'&&pricingPreviewCallbacks[0].payload.vat_rate==='20'&&pricingNodes['#contract-vat-amount'].textContent==='À calculer'&&pricingNodes['#contract-annual-ttc'].textContent==='À calculer'&&renderCount===0&&updatePricingCalls===0").toBool())
        e.evaluate("pricingPreviewCallbacks[0].done({ok:true,vat_amount:'20,00',annual_ttc:'120,00'});")
        self.assertTrue(e.evaluate("pricingNodes['#contract-vat-amount'].textContent==='20,00 €'&&pricingNodes['#contract-annual-ttc'].textContent==='120,00 €'&&renderCount===0").toBool())
        e.evaluate("pricingNodes['#contract-vat-rate'].value='10';pricingNodes['#contract-vat-rate'].listeners.change();")
        self.assertTrue(e.evaluate("previewPricingCalls===2&&pricingPreviewCallbacks[1].payload.annual_ht==='100'&&pricingPreviewCallbacks[1].payload.vat_rate==='10'").toBool())

    def test_pricing_preview_rejects_invalid_and_stale_callbacks(self):
        e = self.engine(include_pricing_preview=True)
        e.evaluate("installPricingPreviewDom();pricingNodes['#contract-annual-ht'].value='';requestContractPricingPreview();pricingPreviewCallbacks[0].done({ok:false});")
        self.assertTrue(e.evaluate("pricingNodes['#contract-vat-amount'].textContent==='À calculer'&&pricingNodes['#contract-annual-ttc'].textContent==='À calculer'&&renderCount===0&&updatePricingCalls===0").toBool())
        e.evaluate("pricingNodes['#contract-annual-ht'].value='100';pricingNodes['#contract-vat-rate'].value='';requestContractPricingPreview();pricingPreviewCallbacks[1].done({ok:false});")
        self.assertTrue(e.evaluate("pricingNodes['#contract-vat-amount'].textContent==='À calculer'&&pricingNodes['#contract-annual-ttc'].textContent==='À calculer'").toBool())
        e.evaluate("pricingNodes['#contract-vat-rate'].value='20';requestContractPricingPreview();pricingNodes['#contract-vat-rate'].value='10';requestContractPricingPreview();pricingPreviewCallbacks[3].done({ok:true,vat_amount:'10,00',annual_ttc:'110,00'});pricingPreviewCallbacks[2].done({ok:true,vat_amount:'20,00',annual_ttc:'120,00'});")
        self.assertTrue(e.evaluate("pricingNodes['#contract-vat-amount'].textContent==='10,00 €'&&pricingNodes['#contract-annual-ttc'].textContent==='110,00 €'").toBool())
        e.evaluate("pricingNodes['#contract-annual-ht'].value='200';requestContractPricingPreview();step2PreviewRenderEpoch+=1;pricingPreviewCallbacks[4].done({ok:true,vat_amount:'40,00',annual_ttc:'240,00'});")
        self.assertTrue(e.evaluate("pricingNodes['#contract-vat-amount'].textContent==='À calculer'&&pricingNodes['#contract-annual-ttc'].textContent==='À calculer'").toBool())

    def test_pricing_preview_respects_contract_and_session_authority_without_js_calculation(self):
        e = self.engine(include_pricing_preview=True)
        e.evaluate("installPricingPreviewDom();var pricing=step2BlockState('PRICING');pricing.mode='SAVED';requestContractPricingPreview();contractWorkspaceState.contract.editable=false;pricing.mode='EDITING';requestContractPricingPreview();")
        self.assertTrue(e.evaluate("previewPricingCalls===0").toBool())
        e.evaluate("contractWorkspaceState.contract.editable=true;requestContractPricingPreview();contractWorkspaceState.contract.id='contract-B';pricingPreviewCallbacks[0].done({ok:true,vat_amount:'20,00',annual_ttc:'120,00'});")
        self.assertTrue(e.evaluate("pricingNodes['#contract-vat-amount'].textContent==='À calculer'&&pricingNodes['#contract-annual-ttc'].textContent==='À calculer'").toBool())
        start = self.js.index("function requestContractPricingPreview")
        end = self.js.index("function wirePricingPreview", start)
        source = self.js[start:end]
        self.assertIn("bridge.previewContractPricing", source)
        self.assertNotIn("updateContractPricing", source)
        self.assertNotIn("renderContractWorkspace", source)
        self.assertNotIn("bridge.refresh", source)
        self.assertNotIn("toFixed", source)
        self.assertNotIn("/ 100", source)
        payment_start = self.js.index("function syncPaymentTermFields")
        payment_end = self.js.index("function saveContractPricing", payment_start)
        self.assertNotIn("requestContractPricingPreview", self.js[payment_start:payment_end])

    def test_service_mixed_authority_presentation_preserves_template_choices(self):
        e = self.engine(False, True)
        e.evaluate("installServiceDom();presentServiceBlock();")
        self.assertTrue(e.evaluate("serviceControls.every(control=>!control.disabled)&&!templateControl.disabled&&serviceActions.innerHTML.includes('Enregistrer les prestations')").toBool())
        e.evaluate("var service=step2BlockState('SERVICE');service.mode='SAVED';service.hasSaved=true;service.feedback='Prestations enregistrées.';presentServiceBlock();")
        self.assertTrue(e.evaluate("serviceControls.every(control=>control.disabled)&&!templateControl.disabled&&serviceActions.innerHTML.includes('Modifier')&&serviceActions.innerHTML.includes('Prestations enregistrées.')").toBool())
        e.evaluate("service.mode='EDITING';serviceActions.innerHTML='<button>Enregistrer les prestations</button>';presentServiceBlock();")
        self.assertTrue(e.evaluate("serviceControls.every(control=>!control.disabled)&&!templateControl.disabled&&serviceActions.innerHTML.includes('Enregistrer les prestations')&&serviceActions.innerHTML.includes('Annuler')").toBool())
        e.evaluate("contractWorkspaceState.contract.editable=false;templateControl.disabled=true;serviceActions.innerHTML='<button>Enregistrer les prestations</button>';presentServiceBlock();")
        self.assertTrue(e.evaluate("serviceControls.every(control=>control.disabled)&&templateControl.disabled&&serviceActions.removed").toBool())

    def test_managed_state_resets_only_after_successful_regime_or_template_change(self):
        e = self.engine(True, False, True)
        ids = ("SERVICE", "PERIOD", "INTERVENTION", "PRICING", "RENEWAL", "SPECIAL_TERMS")
        saved = ";".join(f"var state{index}=step2BlockState('{block}');state{index}.mode='SAVED';state{index}.hasSaved=true;state{index}.feedback='{block} enregistré'" for index, block in enumerate(ids))
        fresh = "&&".join(f"step2BlockState('{block}').mode==='EDITING'&&!step2BlockState('{block}').hasSaved&&step2BlockState('{block}').feedback===''" for block in ids)
        e.evaluate(saved + ";contractWorkspaceState.contract.id='contract-B';step2BlockState('PERIOD').mode='SAVED';contractWorkspaceState.contract.id='contract-A';updateContractRegime('CONSUMER');")
        self.assertTrue(e.evaluate(f"{fresh}&&step2BlockUiState['contract-B'].PERIOD.mode==='SAVED'&&frameworkCalls===1").toBool())
        e.evaluate(saved + ";selectContractTemplateVersion('version-2');")
        self.assertTrue(e.evaluate(f"{fresh}&&templateCalls===1").toBool())
        e.evaluate(saved + ";frameworkResult={ok:false,message:'Erreur'};updateContractRegime('PROFESSIONAL');")
        self.assertTrue(e.evaluate("step2BlockState('SERVICE').mode==='SAVED'&&step2BlockState('PERIOD').feedback==='PERIOD enregistré'").toBool())
        e.evaluate("templateResult={ok:false,message:'Erreur'};selectContractTemplateVersion('version-3');")
        self.assertTrue(e.evaluate("step2BlockState('SERVICE').mode==='SAVED'&&step2BlockState('PERIOD').feedback==='PERIOD enregistré'").toBool())

    def test_review_routes_edit_managed_blocks_transactionally(self):
        e = self.engine(False, False, False, True)
        e.evaluate("var period=step2BlockState('PERIOD');period.mode='SAVED';period.hasSaved=true;period.feedback='Période enregistrée.';openReviewBlock('PERIOD');")
        self.assertTrue(e.evaluate("step2BlockState('PERIOD').mode==='EDITING'&&step2BlockState('PERIOD').hasSaved&&step2BlockState('PERIOD').feedback===''&&reviewCalls===1").toBool())
        for review_id, managed_id in (("PRICE_PAYMENT", "PRICING"), ("MODEL_SERVICES", "SERVICE"), ("SPECIAL_TERMS", "SPECIAL_TERMS")):
            e.evaluate(f"var state=step2BlockState('{managed_id}');state.mode='SAVED';state.hasSaved=true;state.feedback='Sauvegardé';openReviewBlock('{review_id}');")
            self.assertTrue(e.evaluate(f"step2BlockState('{managed_id}').mode==='EDITING'&&step2BlockState('{managed_id}').hasSaved&&step2BlockState('{managed_id}').feedback==='' ").toBool())
        e.evaluate("var renewal=step2BlockState('RENEWAL');renewal.mode='SAVED';renewal.hasSaved=true;renewal.feedback='Avant';reviewResult={ok:false,message:'Route refusée'};openReviewBlock('RENEWAL_END');")
        self.assertTrue(e.evaluate("step2BlockState('RENEWAL').mode==='SAVED'&&step2BlockState('RENEWAL').hasSaved&&step2BlockState('RENEWAL').feedback==='Avant'").toBool())
        e.evaluate("var before=JSON.stringify(step2BlockUiState['contract-A']);reviewResult={ok:true};openReviewBlock('CONTRACT_CONTEXT');")
        self.assertTrue(e.evaluate("JSON.stringify(step2BlockUiState['contract-A'])===before").toBool())

    def test_executable_new_managed_blocks_preserve_local_sessions(self):
        e = self.engine(True)
        blocks = (
            ("SERVICE", "Prestations enregistrées."),
            ("INTERVENTION", "Conditions d’intervention enregistrées."),
            ("PRICING", "Prix et paiement enregistrés."),
            ("RENEWAL", "Renouvellement enregistré."),
            ("SPECIAL_TERMS", "Conditions particulières enregistrées."),
        )
        for block, feedback in blocks:
            self.assertTrue(e.evaluate(f"step2BlockState('{block}').mode==='EDITING'&&!step2BlockState('{block}').hasSaved").toBool())
            e.evaluate(f"contractMutation(function(done){{done({{ok:true,message:{feedback!r}}});}},true,{feedback!r},{{step2BlockId:'{block}'}});")
            self.assertTrue(e.evaluate(f"step2BlockState('{block}').mode==='SAVED'&&step2BlockState('{block}').hasSaved&&step2BlockState('{block}').feedback==={feedback!r}").toBool())
            e.evaluate(f"editStep2Block('{block}');")
            self.assertTrue(e.evaluate(f"step2BlockState('{block}').mode==='EDITING'&&step2BlockState('{block}').hasSaved&&step2BlockState('{block}').feedback===''").toBool())
            e.evaluate(f"cancelStep2Block('{block}');")
            self.assertTrue(e.evaluate(f"step2BlockState('{block}').mode==='SAVED'").toBool())
            e.evaluate(f"editStep2Block('{block}');contractMutation(function(done){{done({{ok:false,message:'Erreur'}});}},true,{feedback!r},{{step2BlockId:'{block}'}});")
            self.assertTrue(e.evaluate(f"step2BlockState('{block}').mode==='EDITING'&&step2BlockState('{block}').feedback===''").toBool())
        self.assertTrue(e.evaluate("globalFeedbackCount===0&&updatePeriodCalls===0&&previewPeriodCalls===0").toBool())

    def test_cross_block_isolation_and_rendering_for_new_managed_blocks(self):
        e = self.engine(False, True)
        markup = '<article class="contract-section test-section"><input value="x"><textarea>notes</textarea><div class="conditions-actions"><button class="primary">Enregistrer</button></div></article>'
        e.evaluate("var blockMarkup=" + repr(markup) + ";")
        e.evaluate("step2BlockState('PERIOD').mode='SAVED';step2BlockState('PERIOD').hasSaved=true;step2BlockState('PRICING').mode='SAVED';step2BlockState('PRICING').hasSaved=true;")
        e.evaluate("editStep2Block('PRICING');")
        self.assertTrue(e.evaluate("step2BlockState('PERIOD').mode==='SAVED'&&step2BlockState('PRICING').mode==='EDITING'&&step2BlockState('INTERVENTION').mode==='EDITING'&&!step2BlockState('INTERVENTION').hasSaved").toBool())
        for block in ("INTERVENTION", "PRICING", "RENEWAL", "SPECIAL_TERMS"):
            e.evaluate(f"var current=step2BlockState('{block}');current.mode='EDITING';current.hasSaved=false;current.feedback='';")
            fresh = e.evaluate(f"presentManagedStep2Block('{block}', blockMarkup)").toString()
            self.assertIn("Enregistrer", fresh); self.assertNotIn("Modifier", fresh); self.assertNotIn("Annuler", fresh)
            e.evaluate(f"var current=step2BlockState('{block}');current.mode='SAVED';current.hasSaved=true;current.feedback='{block} enregistré';")
            saved = e.evaluate(f"presentManagedStep2Block('{block}', blockMarkup)").toString()
            self.assertIn("Modifier", saved); self.assertIn(f"{block} enregistré", saved); self.assertNotIn(">Enregistrer<", saved)
            self.assertIn("disabled", saved); self.assertIn('data-step2-block="' + block + '"', saved)
            e.evaluate(f"editStep2Block('{block}');")
            editing = e.evaluate(f"presentManagedStep2Block('{block}', blockMarkup)").toString()
            self.assertIn("Enregistrer", editing); self.assertIn("Annuler", editing); self.assertNotIn("Modifier", editing)
        e.evaluate("contractWorkspaceState.contract.editable=false;")
        for block in ("INTERVENTION", "PRICING", "RENEWAL", "SPECIAL_TERMS"):
            readonly = e.evaluate(f"presentManagedStep2Block('{block}', blockMarkup)").toString()
            for forbidden in ("Modifier", "Enregistrer", "Annuler", f"{block} enregistré"):
                self.assertNotIn(forbidden, readonly)

    def test_new_save_functions_use_managed_local_feedback(self):
        for function, block in (
            ("saveContractServiceOffer", "SERVICE"),
            ("saveContractInterventionConditions", "INTERVENTION"),
            ("saveContractPricing", "PRICING"),
            ("saveContractRenewal", "RENEWAL"),
            ("saveContractSpecialTerms", "SPECIAL_TERMS"),
        ):
            start = self.js.index(f"function {function}")
            end = self.js.index("\n}\n\nfunction", start) + 2
            source = self.js[start:end]
            self.assertIn(f"step2BlockId: '{block}'", source)
            self.assertNotIn("showContractSaveFeedback", source)
