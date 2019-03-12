# -*- coding: utf-8 -*-
# © 2012 Nicolas Bessi (Camptocamp SA)
# © 2015 Yannick Vaucher (Camptocamp SA)
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
from odoo import models, api, _
from odoo.tools import mod10r
from odoo import exceptions


class AccountInvoice(models.Model):

    _inherit = "account.invoice"

    def _search(self, args, offset=0, limit=None, order=None, count=False,
                access_rights_uid=None):
        domain = []
        for arg in args:
            if not isinstance(arg, (tuple, list)) or len(arg) != 3:
                domain.append(arg)
                continue
            field, operator, value = arg
            if field != 'reference':
                domain.append(arg)
                continue
            if operator not in ('like', 'ilike', '=like', '=ilike',
                                'not like', 'not ilike'):
                domain.append(arg)
                continue
            if value:
                value = value.replace(' ', '')
                if not value:
                    # original value contains only spaces, the query
                    # would return all rows, so avoid a costly search
                    # and drop the domain triplet
                    continue
                # add wildcards for the like search, except if the operator
                # is =like of =ilike because they are supposed to be there yet
                if operator.startswith('='):
                    operator = operator[1:]
                else:
                    value = '%%%s%%' % (value,)
            query = ("SELECT id FROM account_invoice "
                     "WHERE REPLACE(reference, ' ', '') %s %%s" %
                     (operator,))
            self.env.cr.execute(query, (value,))
            ids = [t[0] for t in self.env.cr.fetchall()]
            domain.append(('id', 'in', ids))

        return super(AccountInvoice, self)._search(
            domain, offset=offset, limit=limit, order=order, count=count,
            access_rights_uid=access_rights_uid)

    @api.model
    def _get_reference_type(self):
        selection = super(AccountInvoice, self)._get_reference_type()
        selection.append(('bvr', _('BVR Reference')))
        return selection

    @api.onchange('reference')
    def onchange_reference(self):
        """Identify if the reference entered is of type BVR
        if it does, change reference_type"""
        if len(self.reference or '') == 27:
            try:
                self._is_bvr_reference()
            except exceptions.ValidationError:
                return
            self.reference_type = 'bvr'

    @api.constrains('reference_type')
    def _check_bank_type_for_type_bvr(self):
        for invoice in self:
            if invoice.reference_type == 'bvr':
                bank_acc = invoice.partner_banks_to_show()
                if not bank_acc:
                    user = self.env.user
                    bank_ids = user.company_id.partner_id.bank_ids
                    if bank_ids:
                        bank_acc = bank_ids[0]
                if not (bank_acc.acc_type == 'postal' or
                        bank_acc.acc_type != 'postal' and
                        (bank_acc.ccp or bank_acc.bank_id.ccp)):
                    if invoice.type in ('in_invoice', 'in_refund'):
                        raise exceptions.ValidationError(
                            _('BVR/ESR Reference type needs a postal account'
                              ' number on the customer.')
                        )
                    else:
                        raise exceptions.ValidationError(
                            _('BVR/ESR Reference type needs a postal account'
                              ' number on your company')
                        )
        return True

    @api.multi
    def _is_bvr_reference(self):
        """
        Function to validate a bvr reference like :
        0100054150009>132000000000000000000000014+ 1300132412>
        The validation is based on l10n_ch
        """
        if not self.reference:
            raise exceptions.ValidationError(
                _('BVR/ESR Reference is required')
            )
        # In this case
        # <010001000060190> 052550152684006+ 43435>
        # the reference 052550152684006 do not match modulo 10
        #
        if (mod10r(self.reference[:-1]) != self.reference and
                len(self.reference) == 15):
            return True
        #
        if mod10r(self.reference[:-1]) != self.reference:
            raise exceptions.ValidationError(
                _('Invalid BVR/ESR Number (wrong checksum).')
            )

    @api.constrains('reference')
    def _check_bvr(self):
        """ Do the check only for invoice with reference_type = 'bvr' """
        for invoice in self:
            if invoice.reference_type == 'bvr':
                invoice._is_bvr_reference()
        return True

    def write(self, vals):
        """Override to update partner_bank_id before constraints if needed and
        to be consistent with create
        """
        if not self.partner_bank_id or not vals.get('partner_bank_id'):
            type_defined = vals.get('type') or self.type
            if type_defined == 'out_invoice':
                partner = self.env.user.company_id.partner_id
                journal = vals.get('journal_id') or self.journal_id.id
                ref_type = vals.get('reference_type') or self.reference_type
                vals['partner_bank_id'] = self._get_bank_id(
                    partner, journal, ref_type,
                )
        return super().write(vals)

    @api.model
    def create(self, vals):
        """We override create in order to have customer invoices
        generated by the comercial flow as on change partner is
        not systemtically call"""
        type_defined = vals.get('type') or self.env.context.get('type', False)
        if type_defined == 'out_invoice' and not vals.get('partner_bank_id'):
            partner = self.env.user.company_id.partner_id
            vals['partner_bank_id'] = self._get_bank_id(
                partner, vals.get('journal_id'), vals.get('reference_type'),
            )
        return super(AccountInvoice, self).create(vals)

    def _get_bank_id(self, partner, journal_id, ref_type):
        if journal_id:
            return self.env['account.journal'].browse(journal_id). \
                bank_account_id.id
        if ref_type == 'isr':
            bank_ids = partner.bank_ids.filtered(
                lambda s: s.acc_type == 'postal'
            )
        else:
            bank_ids = partner.bank_ids
        if bank_ids:
            return bank_ids[0].id
