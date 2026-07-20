# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
import logging

_logger = logging.getLogger(__name__)

APPOINTMENT_TYPE_ID = 3
EDL_PRODUCT_TEMPLATE_ID = 294
EXPERTS = {
    'ceg@berex.be': 7,
    'cd@berex.be':  74,
    'yvk@berex.be': 2,
}
NOTIF_EMAIL = 'sc@berex.be'
SENDER_EMAIL = 'noreply@berex.be'
CONFIRM_SENDER = 'rs@berex.be'
EDL_TYPE_DISPLAY = {
    'Residentiel':   'Résidentiel',
    'Commerce':      'Commerce',
    'Office':        'Office',
    'Logistique':    'Logistique',
    'Avant Travaux': 'Avant Travaux',
}

class BerexRdvEDL(http.Controller):

    @http.route('/berex/rdv/submit', type='json', auth='public', methods=['POST'], csrf=False, website=True)
    def rdv_submit(self, **kwargs):
        try:
            data = request.get_json_data()
            return self._process_rdv(data)
        except Exception as e:
            _logger.exception("Erreur RDV EDL submit : %s", e)
            return {'success': False, 'error': str(e)}

    @http.route('/berex/rdv/taken_slots', type='json', auth='public', methods=['POST'], csrf=False, website=True)
    def taken_slots(self, **kwargs):
        try:
            data = request.get_json_data()
            start = data.get('start', '')
            stop  = data.get('stop',  '')
            expert_user_ids = list(EXPERTS.values())
            events = request.env['calendar.event'].sudo().search_read(
                domain=[['start','<=',stop],['stop','>=',start],['user_id','in',expert_user_ids]],
                fields=['start','stop','user_id'],
                limit=500,
            )
            uid_to_email = {v: k for k, v in EXPERTS.items()}
            result = []
            for ev in events:
                uid = ev['user_id'][0] if isinstance(ev['user_id'], (list,tuple)) else ev['user_id']
                email = uid_to_email.get(uid, '')
                if email:
                    result.append({'start':str(ev['start']),'stop':str(ev['stop']),'expertEmail':email})
            return {'success': True, 'slots': result}
        except Exception as e:
            _logger.exception("Erreur taken_slots : %s", e)
            return {'success': False, 'slots': []}

    def _process_rdv(self, data):
        env = request.env
        edl_type         = data.get('edlType','')
        edl_type_display = EDL_TYPE_DISPLAY.get(edl_type, edl_type)
        selected_start   = data.get('selectedStart','')
        selected_stop    = data.get('selectedStop','')
        slot_label       = data.get('slotLabel','')
        expert_email     = data.get('expertEmail','').lower()
        expert_name      = data.get('expertName','')
        bien_adresse     = data.get('bienAdresse','')
        remarques        = data.get('remarques','')
        intervention     = data.get('intervention','')
        calculated_price = data.get('calculatedPrice')
        is_quote         = data.get('isQuote', False)
        is_pro           = edl_type in ('Commerce','Office','Logistique')
        prop_nom         = data.get('propNom','') or data.get('atNom','')
        prop_email       = data.get('propEmail','')
        prop_tel         = data.get('propTel','')
        locataires       = data.get('locataires',[])
        contact_nom      = data.get('contactNom','')
        contact_email    = data.get('contactEmail','')
        contact_tel      = data.get('contactTel','')
        agence_val       = data.get('agence','Non')
        agence_nom       = data.get('agenceNom','')
        agence_tel       = data.get('agenceTel','')
        agence_email     = data.get('agenceEmail','')
        at_detail        = data.get('atDetail','')
        files_bail       = data.get('filesBail',[])
        files_edl_entree = data.get('filesEdlEntree',[])

        prop_partner_id    = self._find_or_create_partner(env, prop_email, prop_nom, prop_tel)
        loc_partner_ids    = []
        for loc in locataires:
            pid = self._find_or_create_partner(env, loc.get('email',''), loc.get('nom',''), loc.get('tel',''))
            if pid:
                loc_partner_ids.append(pid)
        contact_partner_id = self._find_or_create_partner(env, contact_email, contact_nom, contact_tel)
        expert_user_id     = EXPERTS.get(expert_email)
        expert_partner_id  = None
        if expert_user_id:
            user = env['res.users'].sudo().browse(expert_user_id)
            if user.exists():
                expert_partner_id = user.partner_id.id

        titre = self._build_title(edl_type_display, intervention, prop_nom, edl_type)
        cal_desc = self._build_description(edl_type_display, intervention, bien_adresse, prop_nom, prop_tel, prop_email, locataires, agence_val, agence_nom, agence_tel, agence_email, contact_nom, contact_tel, contact_email, remarques, at_detail)

        cal_vals = {'name':titre,'start':selected_start,'stop':selected_stop,'description':cal_desc,'appointment_type_id':APPOINTMENT_TYPE_ID}
        if expert_user_id: cal_vals['user_id'] = expert_user_id
        if bien_adresse: cal_vals['x_studio_adresse_du_bien'] = bien_adresse
        if remarques: cal_vals['x_studio_remarques'] = remarques

        event = env['calendar.event'].sudo().with_context(no_mail=True,mail_notify_force_send=False,mail_auto_subscribe_no_notify=True).create(cal_vals)
        event_id = event.id

        partner_ids_to_add = []
        if expert_partner_id: partner_ids_to_add.append((4, expert_partner_id))
        if contact_partner_id: partner_ids_to_add.append((4, contact_partner_id))
        if prop_partner_id and prop_partner_id != contact_partner_id: partner_ids_to_add.append((4, prop_partner_id))
        for lp in loc_partner_ids: partner_ids_to_add.append((4, lp))
        if partner_ids_to_add:
            event.sudo().with_context(no_mail=True,mail_notify_force_send=False,mail_auto_subscribe_no_notify=True,no_calendar_sync=True).write({'partner_ids':partner_ids_to_add})

        for f in files_bail: self._create_attachment(env, event_id, 'Contrat de bail', f)
        for f in files_edl_entree: self._create_attachment(env, event_id, "EDL d'entrée", f)

        if edl_type != 'Avant Travaux' and not is_quote and calculated_price:
            product_id = self._resolve_product_id(env)
            tax_id     = self._resolve_tax_id(env, price_include=(not is_pro))
            tax_cmd    = [(6,0,[tax_id])] if tax_id else []
            prix       = round(calculated_price*1.21) if is_pro else calculated_price
            inv_label  = 'EDL {} — {}'.format(edl_type_display, bien_adresse)
            inv_prop_id = self._create_invoice(env, prop_partner_id, product_id, inv_label+' — Part propriétaire', prix, tax_cmd) if prop_partner_id else None
            first_loc_id = loc_partner_ids[0] if loc_partner_ids else None
            inv_loc_id  = self._create_invoice(env, first_loc_id, product_id, inv_label+' — Part locataire', prix, tax_cmd) if first_loc_id else None
            link_vals = {}
            if inv_prop_id: link_vals['x_studio_facture_proprietaire'] = inv_prop_id
            if inv_loc_id:  link_vals['x_studio_facture_locataire']    = inv_loc_id
            if link_vals:   event.sudo().write(link_vals)

        body_html = self._build_internal_email(edl_type_display, slot_label, expert_name, calculated_price, is_quote, is_pro, contact_nom, contact_email, contact_tel, bien_adresse, intervention, data, locataires, agence_val, agence_nom, agence_tel, agence_email, remarques)
        mail_int = env['mail.mail'].sudo().create({'subject':'{} — {}'.format(titre,slot_label),'email_to':NOTIF_EMAIL,'email_from':SENDER_EMAIL,'body_html':body_html})
        mail_int.sudo().send()

        recipients = [contact_email]
        if prop_email and prop_email != contact_email: recipients.append(prop_email)
        confirm_html = self._build_confirm_email(edl_type_display, bien_adresse, slot_label)
        mail_conf = env['mail.mail'].sudo().create({'subject':'Votre demande de rendez-vous Berex a bien été reçue','email_to':','.join(recipients),'email_from':CONFIRM_SENDER,'body_html':confirm_html})
        mail_conf.sudo().send()
        return {'success': True}

    def _find_or_create_partner(self, env, email, name, phone=None):
        if not email: return None
        existing = env['res.partner'].sudo().search([['email','=ilike',email]], limit=1)
        if existing: return existing.id
        vals = {'name': name or email, 'email': email}
        if phone: vals['phone'] = phone
        return env['res.partner'].sudo().create(vals).id

    def _create_attachment(self, env, event_id, label, file_dict):
        if not file_dict or not file_dict.get('data'): return None
        try:
            env['ir.attachment'].sudo().create({'name':'{} — {}'.format(label,file_dict.get('name','fichier')),'datas':file_dict['data'],'res_model':'calendar.event','res_id':event_id,'mimetype':file_dict.get('mime','application/octet-stream')})
        except Exception as e:
            _logger.warning("Attachment error: %s", e)

    def _resolve_product_id(self, env):
        p = env['product.product'].sudo().search([['product_tmpl_id','=',EDL_PRODUCT_TEMPLATE_ID]], limit=1)
        return p.id if p else None

    def _resolve_tax_id(self, env, price_include=True):
        t = env['account.tax'].sudo().search([['type_tax_use','=','sale'],['amount','=',21],['price_include','=',price_include],['active','=',True]], limit=1)
        if not t:
            t = env['account.tax'].sudo().search([['type_tax_use','=','sale'],['amount','=',21],['active','=',True]], limit=1)
        return t.id if t else None

    def _create_invoice(self, env, partner_id, product_id, label, price, tax_cmd):
        try:
            move = env['account.move'].sudo().create({'move_type':'out_invoice','partner_id':partner_id,'invoice_line_ids':[(0,0,{'product_id':product_id or False,'name':label,'quantity':1,'price_unit':price,'tax_ids':tax_cmd})]})
            return move.id
        except Exception as e:
            _logger.warning("Invoice error: %s", e)
            return None

    @staticmethod
    def _build_title(edl_type_display, intervention, prop_nom, edl_type):
        if edl_type == 'Avant Travaux': return 'Avant Travaux — {}'.format(prop_nom)
        parts = ['EDL', edl_type_display]
        if intervention: parts.append('({})'.format(intervention))
        parts += ['—', prop_nom]
        return ' '.join(parts)

    @staticmethod
    def _build_description(edl_type_display, intervention, bien_adresse, prop_nom, prop_tel, prop_email, locataires, agence_val, agence_nom, agence_tel, agence_email, contact_nom, contact_tel, contact_email, remarques, at_detail):
        lines = ['-- MISSION --','Type : {}'.format(edl_type_display)]
        if intervention: lines.append('Intervention : {}'.format(intervention))
        if bien_adresse: lines += ['','-- ADRESSE --', bien_adresse]
        lines += ['','-- PROPRIÉTAIRE --','{} / {} / {}'.format(prop_nom,prop_tel,prop_email)]
        if locataires:
            lines += ['','-- LOCATAIRE(S) --']
            for loc in locataires:
                lines.append('{} / {} / {}'.format(loc.get('nom',''),loc.get('tel',''),loc.get('email','')))
        if agence_val == 'Oui':
            lines += ['','-- AGENCE --','{} / {} / {}'.format(agence_nom,agence_tel,agence_email)]
        lines += ['','-- DEMANDEUR --','{} / {} / {}'.format(contact_nom,contact_tel,contact_email)]
        if at_detail: lines += ['','-- DÉTAIL AVANT TRAVAUX --', at_detail]
        if remarques: lines += ['','-- REMARQUES --', remarques]
        return '\n'.join(lines)

    @staticmethod
    def _build_internal_email(edl_type_display, slot_label, expert_name, calculated_price, is_quote, is_pro, contact_nom, contact_email, contact_tel, bien_adresse, intervention, data, locataires, agence_val, agence_nom, agence_tel, agence_email, remarques):
        def esc(s): return (str(s) if s else '').replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
        def euro(n):
            try: return '{:,.0f} €'.format(n).replace(',','\u202f')
            except: return '{} €'.format(n)
        html = '<div style="font-family:Arial,sans-serif;max-width:640px;padding:24px;background:#f2f7f5;border-radius:12px;">'
        html += '<h2 style="color:#1a4a3a;border-bottom:2px solid #3ecf8e;padding-bottom:10px;">📋 Nouvelle demande — État des lieux</h2>'
        html += '<p><strong>Type :</strong> {}</p><p><strong>Créneau :</strong> {}</p>'.format(esc(edl_type_display),esc(slot_label))
        if expert_name: html += '<p><strong>Expert :</strong> {}</p>'.format(esc(expert_name))
        if calculated_price and not is_quote:
            html += '<p><strong>Estimation :</strong> {}</p>'.format(euro(calculated_price*2) if not is_pro else euro(calculated_price)+' HTVA/partie')
        html += '<hr/><h3 style="color:#3ecf8e;">Demandeur</h3>'
        html += '<p>{} — {} — {}</p>'.format(esc(contact_nom),esc(contact_email),esc(contact_tel))
        prop_nom = data.get('propNom','') or data.get('atNom','')
        if prop_nom: html += '<hr/><h3 style="color:#3ecf8e;">Propriétaire</h3><p>{} — {} — {}</p>'.format(esc(prop_nom),esc(data.get('propEmail','')),esc(data.get('propTel','')))
        if locataires:
            html += '<h3 style="color:#3ecf8e;">Locataire(s)</h3>'
            for loc in locataires: html += '<p>{} — {} — {}</p>'.format(esc(loc.get('nom','')),esc(loc.get('email','')),esc(loc.get('tel','')))
        if bien_adresse: html += '<p><strong>Adresse :</strong> {}</p>'.format(esc(bien_adresse))
        if remarques: html += '<p><strong>Remarques :</strong> {}</p>'.format(esc(remarques))
        html += '</div>'
        return html

    @staticmethod
    def _build_confirm_email(edl_type_display, bien_adresse, slot_label):
        def esc(s): return (str(s) if s else '').replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
        adresse_phrase = ' au <strong>{}</strong>'.format(esc(bien_adresse)) if bien_adresse else ''
        return ('<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;border:1px solid #e0ece7;">'
            '<div style="background:#1a4a3a;padding:28px 32px;text-align:center;"><img src="https://www.berex.be/web/image/website/1/logo/Berex?unique=541a132" alt="Berex" style="height:40px;filter:brightness(0) invert(1);"/></div>'
            '<div style="padding:36px 32px;">'
            '<p style="font-size:15px;color:#0d1f18;line-height:1.7;margin:0 0 12px;">Nous avons bien reçu votre demande pour un <strong>état des lieux {}</strong>{}.'.format(esc(edl_type_display),adresse_phrase)+'</p>'
            '<p style="font-size:15px;color:#0d1f18;line-height:1.7;margin:0 0 24px;">Créneau souhaité : <strong>{}</strong>.<br/>Nous vous confirmerons dans les plus brefs délais.</p>'.format(esc(slot_label))
            '<div style="background:#f2f7f5;border-radius:10px;padding:18px 20px;margin-bottom:28px;border-left:4px solid #3ecf8e;">'
            '<p style="margin:0;font-size:13.5px;color:#1a4a3a;">📞 <strong>+32 2 886 02 75</strong><br/>✉️ <a href="mailto:info@berex.be" style="color:#1a4a3a;">info@berex.be</a></p></div>'
            '<p style="font-size:14px;color:#6b8c7e;">Cordialement,<br/><strong style="color:#1a4a3a;">L\'équipe Berex</strong></p></div>'
            '<div style="background:#f2f7f5;padding:18px 32px;text-align:center;border-top:1px solid #e0ece7;">'
            '<p style="font-size:11px;color:#6b8c7e;margin:0;">Berex srl — Avenue Louis Lepoutre n°38 — 1050 Bruxelles<br/>'
            '<a href="https://www.berex.be" style="color:#1a4a3a;">www.berex.be</a></p></div></div>')
