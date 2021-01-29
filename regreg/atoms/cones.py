from copy import copy
import warnings

from scipy import sparse
import numpy as np

from ..problems.composite import nonsmooth, smooth_conjugate
from ..affine import linear_transform, identity as identity_transform
from ..identity_quadratic import identity_quadratic
from ..smooth import affine_smooth
from ..atoms import _work_out_conjugate, atom
from ..objdoctemplates import objective_doc_templater
from ..doctemplates import (doc_template_user, doc_template_provider)

from .projl1_cython import projl1_epigraph

#TODO use doctemplaters

@objective_doc_templater()
class cone(atom):

    """
    A class that defines the API for cone constraints.
    """

    tol = 1.0e-05

    def __eq__(self, other):
        if self.__class__ == other.__class__:
            return self.shape == other.shape
        return False

    def __copy__(self):
        return self.__class__(copy(self.shape),
                              offset=copy(self.offset),
                              initial=self.coefs,
                              quadratic=self.quadratic)
    
    def __repr__(self):
        if self.quadratic.iszero:
            return "%s(%s, offset=%s)" % \
                (self.__class__.__name__,
                 repr(self.shape), 
                 str(self.offset))
        else:
            return "%s(%s, offset=%s, quadratic=%s)" % \
                (self.__class__.__name__,
                 repr(self.shape), 
                 str(self.offset),
                 str(self.quadratic))

    @property
    def conjugate(self):
        if self.quadratic.coef == 0:
            offset, outq = _work_out_conjugate(self.offset, 
                                               self.quadratic)
            cls = conjugate_cone_pairs[self.__class__]
            atom = cls(self.shape, 
                       offset=offset,
                       quadratic=outq)
        else:
            atom = smooth_conjugate(self)
        self._conjugate = atom
        self._conjugate._conjugate = self
        return self._conjugate

    @property
    def dual(self):
        return self.linear_transform, self.conjugate

    @property
    def linear_transform(self):
        if not hasattr(self, "_linear_transform"):
            self._linear_transform = identity_transform(self.shape)
        return self._linear_transform
    
    @doc_template_provider
    def constraint(self, x):
        """
        The constraint

        .. math::

           %(objective)s
        """
        raise NotImplementedError

    def latexify(self, var='x', idx=''):
        d = {}
        if self.offset is None:
            d['var'] = var
        else:
            d['var'] = var + r'+\alpha_{%s}' % str(idx)

        obj = self.objective_template % d

        if not self.quadratic.iszero:
            return ' + '.join([self.quadratic.latexify(var=var,idx=idx),obj])
        return obj

    @doc_template_provider
    def nonsmooth_objective(self, x, check_feasibility=False):
        x_offset = self.apply_offset(x)
        if check_feasibility:
            v = self.constraint(x_offset)
        else:
            v = 0
        v += self.quadratic.objective(x, 'func')
        return v

    @doc_template_provider
    def proximal(self, proxq, prox_control=None):
        r"""
        The proximal operator. If the atom is in
        Lagrange mode, this has the form

        .. math::

           v^{\lambda}(x) = \text{argmin}_{v \in \mathbb{R}^p} \frac{L}{2}
           \|x-v\|^2_2 + \lambda h(v+\alpha) + \langle v, \eta \rangle

        where :math:`\alpha` is the offset of self.affine_transform and
        :math:`\eta` is self.linear_term.

        .. math::

           v^{\lambda}(x) = \text{argmin}_{v \in \mathbb{R}^p} \frac{L}{2}
           \|x-v\|^2_2 + \langle v, \eta \rangle \text{s.t.} \   h(v+\alpha) \leq \lambda

        """
        offset, totalq = (self.quadratic + proxq).recenter(self.offset)
        if totalq.coef == 0:
            raise ValueError('lipschitz + quadratic coef must be positive')

        prox_arg = -totalq.linear_term / totalq.coef

        debug = False
        if debug:
            print '='*80
            print 'x :', x
            print 'grad: ', grad
            print 'cone: ', self
            print 'proxq: ', proxq
            print 'proxarg: ', prox_arg
            print 'totalq: ', totalq

        eta = self.cone_prox(prox_arg)
        if offset is None:
            return eta
        else:
            return eta - offset

    @doc_template_provider
    def cone_prox(self, x):
        r"""
        Return (unique) minimizer

        .. math::

           %(var)s^{\lambda}(u) = \text{argmin}_{%(var)s \in \mathbb{R}^p} \frac{1}{2}
           \|%(var)s-u\|^2_2 + h(%(var)s)

        where $p$=x.shape[0] and :math:`h(%(var)s)` is `self`, a cone constraint.
        For this instance,

        .. math::

            h(%(var)s) = %(objective)s

        """
        raise NotImplementedError

    @classmethod
    def linear(cls, linear_operator, diag=False,
               offset=None,
               quadratic=None):
        if not isinstance(linear_operator, linear_transform):
            l = linear_transform(linear_operator, diag=diag)
        else:
            l = linear_operator
        cone = cls(l.output_shape, 
                   offset=offset,
                   quadratic=quadratic)
        return affine_cone(cone, l)

    @classmethod
    def affine(cls, linear_operator, offset, diag=False,
               quadratic=None):
        if not isinstance(linear_operator, linear_transform):
            l = linear_transform(linear_operator, diag=diag)
        else:
            l = linear_operator
        cone = cls(l.output_shape, 
                   offset=offset,
                   quadratic=quadratic)
        return affine_cone(cone, l)


class affine_cone(object):
    r"""
    Given a seminorm on :math:`\mathbb{R}^p`, i.e.  :math:`\beta \mapsto
    h_K(\beta)` this class creates a new seminorm that evaluates
    :math:`h_K(D\beta+\alpha)`

    This class does not have a prox, but its dual does. The prox of the dual is

    .. math::

       \text{minimize} \frac{1}{2} \|y-x\|^2_2 + x^T\alpha
       \ \text{s.t.} \ x \in \lambda K

    """

    def __init__(self, cone_obj, atransform):
        self.cone = copy(cone_obj)
        # if the affine transform has an offset, put it into
        # the cone. in this way, the affine_transform is actually
        # always linear
        if atransform.affine_offset is not None:
            self.cone.offset += atransform.affine_offset
            ltransform = affine_transform(atransform.linear_operator, None,
                                          diag=atransform.diag)
        else:
            ltransform = atransform
        self.linear_transform = ltransform
        self.shape = self.linear_transform.output_shape

    def __repr__(self):
        return "affine_cone(%s, %s)" % (`self.cone`,
                                        `self.linear_transform.linear_operator`)

    def latexify(self, var='x', idx=''):
        return self.cone.latexify(var='D_{%s}%s' % (idx, x), idx=idx)

    @property
    def dual(self):
        return self.linear_transform, self.cone.conjugate

    def nonsmooth_objective(self, x, check_feasibility=False):
        """
        Return self.cone.nonsmooth_objective(self.linear_transform.linear_map(x))
        """
        return self.cone.nonsmooth_objective(self.linear_transform.linear_map(x),
                                             check_feasibility=check_feasibility)

    def smoothed(self, smoothing_quadratic):
        '''
        Add quadratic smoothing term
        '''
        ltransform, conjugate_atom = self.dual
        if conjugate_atom.quadratic is not None:
            total_q = smoothing_quadratic + conjugate_atom.quadratic
        else:
            total_q = smoothing_quadratic
        if total_q.coef in [None, 0]:
            raise ValueError('quadratic term of smoothing_quadratic must be non 0')
        conjugate_atom.quadratic = total_q
        smoothed_atom = conjugate_atom.conjugate
        return affine_smooth(smoothed_atom, ltransform)


class nonnegative(cone):
    """
    The non-negative cone constraint (which is the support
    function of the non-positive cone constraint).
    """

    objective_template = r"""I^{\infty}(%(var)s \succeq 0)"""

    @doc_template_user
    def constraint(self, x):
        tol_lim = np.fabs(x).max() * self.tol
        incone = np.all(np.greater_equal(x, -tol_lim))
        if incone:
            return 0
        return np.inf

    @doc_template_user
    def cone_prox(self, x):
        return np.maximum(x, 0)


class nonpositive(nonnegative):

    """
    The non-positive cone constraint (which is the support
    function of the non-negative cone constraint).
    """

    objective_template = r"""I^{\infty}(%(var)s \preceq 0)"""

    @doc_template_user
    def constraint(self, x):
        tol_lim = np.fabs(x).max() * self.tol
        incone = np.all(np.less_equal(x, tol_lim))
        if incone:
            return 0
        return np.inf

    @doc_template_user
    def cone_prox(self, x):
        return np.minimum(x, 0)


class zero(cone):
    """
    The zero seminorm, support function of :math:\{0\}
    """

    objective_template = r"""{\cal Z}(%(var)s)"""

    @doc_template_user
    def constraint(self, x):
        return 0.

    @doc_template_user
    def cone_prox(self, x):
        return x

class zero_constraint(cone):
    """
    The zero constraint, support function of :math:`\mathbb{R}`^p
    """

    objective_template = r"""I^{\infty}(%(var)s = 0)"""

    @doc_template_user
    def constraint(self, x):
        if not np.linalg.norm(x) <= self.tol:
            return np.inf
        return 0.

    @doc_template_user
    def cone_prox(self, x):
        return np.zeros(np.asarray(x).shape)

class l2_epigraph(cone):

    """
    The l2_epigraph constraint.
    """

    objective_template = r"""I^{\infty}(%(var)s \in \mathbf{epi}(\ell_2)})"""

    @doc_template_user
    def constraint(self, x):
        
        incone = np.linalg.norm(x[1:]) / x[0] <= 1 + self.tol
        if incone:
            return 0
        return np.inf


    @doc_template_user
    def cone_prox(self, x):
        norm = x[0]
        coef = x[1:]
        norm_coef = np.linalg.norm(coef)
        thold = (norm_coef - norm) / 2.
        result = np.zeros_like(x)
        result[1:] = coef / norm_coef * max(norm_coef - thold, 0)
        result[0] = max(norm + thold, 0)
        return result

class l2_epigraph_polar(cone):

    """
    The polar of the l2_epigraph constraint, which is the negative of the 
    l2 epigraph..
    """

    objective_template = r"""I^{\infty}(-%(var)s \in \mathbf{epi}(\ell_2)})"""

    @doc_template_user
    def constraint(self, x):
        incone = np.linalg.norm(x[1:]) / -x[0] <= 1 + self.tol
        if incone:
            return 0
        return np.inf


    @doc_template_user
    def cone_prox(self, x):
        norm = -x[0]
        coef = -x[1:]
        norm_coef = np.linalg.norm(coef)
        thold = (norm_coef - norm) / 2.
        result = np.zeros_like(x)
        result[1:] = coef / norm_coef * max(norm_coef - thold, 0)
        result[0] = max(norm + thold, 0)
        return x + result


class l1_epigraph(cone):

    """
    The l1_epigraph constraint.
    """

    objective_template = r"""I^{\infty}(%(var)s \in \mathbf{epi}(\ell_1)})"""

    @doc_template_user
    def constraint(self, x):
        incone = np.fabs(x[1:]).sum() / x[0] <= 1 + self.tol
        if incone:
            return 0
        return np.inf


    @doc_template_user
    def cone_prox(self, x):
        return projl1_epigraph(x)

class l1_epigraph_polar(cone):

    """
    The polar l1_epigraph constraint which is just the
    negative of the linf_epigraph.
    """

    objective_template = r"""I^{\infty}(%(var)s  \in -\mathbf{epi}(\ell_{\inf})})"""

    @doc_template_user
    def constraint(self, x):
        
        incone = np.fabs(-x[1:]).max() / -x[0] <= 1 + self.tol
        if incone:
            return 0
        return np.inf


    @doc_template_user
    def cone_prox(self, x):
        return projl1_epigraph(x) - x

class linf_epigraph(cone):

    """
    The $\ell_{\nfty}$ epigraph constraint.
    """

    objective_template = r"""I^{\infty}(%(var)s \in \mathbf{epi}(\ell_1)})"""

    @doc_template_user
    def constraint(self, x):

        incone = np.fabs(x[1:]).max() / x[0] <= 1 + self.tol
        if incone:
            return 0
        return np.inf


    @doc_template_user
    def cone_prox(self, x):

        # we just use the fact that the polar of the linf epigraph is
        # is the negative of the l1 epigraph, so we project
        # -center onto the l1-epigraph and add the result to center...

        return x+projl1_epigraph(-x)


class linf_epigraph_polar(cone):

    """
    The polar linf_epigraph constraint which is just the
    negative of the l1_epigraph.
    """

    objective_template = r"""I^{\infty}(%(var)s \in -\mathbf{epi}(\ell_1)})"""

    @doc_template_user
    def constraint(self, x):
        
        incone = np.fabs(-x[1:]).sum() / -x[0] <= 1 + self.tol
        if incone:
            return 0
        return np.inf


    @doc_template_user
    def cone_prox(self, x):
        return -projl1_epigraph(-x)


conjugate_cone_pairs = {}
for n1, n2 in [(nonnegative,nonpositive),
               (zero, zero_constraint),
               (l1_epigraph, l1_epigraph_polar),
               (l2_epigraph, l2_epigraph_polar),
               (linf_epigraph, linf_epigraph_polar)
               ]:
    conjugate_cone_pairs[n1] = n2
    conjugate_cone_pairs[n2] = n1
