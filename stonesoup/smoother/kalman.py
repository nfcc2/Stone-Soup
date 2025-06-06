import copy
from functools import partial

import numpy as np

from ..base import Property
from ..functions import (gauss2sigma, unscented_transform, cub_points_and_tf)
from ..models.base import LinearModel
from ..models.transition.base import TransitionModel
from ..models.transition.linear import LinearGaussianTransitionModel
from ..types.multihypothesis import MultipleHypothesis
from ..types.prediction import Prediction
from ..types.state import GaussianState
from ..types.update import Update
from .base import Smoother


class KalmanSmoother(Smoother):
    r"""
    The linear-Gaussian or Rauch-Tung-Striebel smoother, colloquially the Kalman smoother [1]_. The
    transition model is therefore linear-Gaussian. No control model is currently implemented.

    TODO: Include a control model

    The smooth function undertakes the backward algorithm on a :class:`~.Track()` object. This is
    done by starting at the final index in the track, :math:`K` and proceeding from
    :math:`K \rightarrow 1` via:

    .. math::

        \mathbf{x}_{k|k-1} &= F_{k} \mathbf{x}_{k-1}

        P_{k|k-1} &= F_{k} P_{k-1} F_{k}^T + Q_{k}

        G_k &= P_{k-1} F_{k}^T P_{k|k-1}^{-1}

        \mathbf{x}_{k-1}^s &= \mathbf{x}_{k-1} + G_k (\mathbf{x}_{k}^s - \mathbf{x}_{k|k-1})

        P_{k-1}^s &= P_{k-1} + G_k (P_{k}^s - P_{k|k-1}) G_k^T

    where :math:`\mathbf{x}_{K}^s = \mathbf{x}_{K}` and :math:`P_K^s = P_K`.

    The predicted state vector and covariance are retrieved from the Track via predicted state or
    updated state via the links therein. Note that this means that the first two equations are not
    calculated, the results merely retrieved. This smoother is therefore strictly Kalman only in
    the backward portion. The prediction might have come by any number of means. If present, the
    transition model (providing :math:`F` and :math:`Q`) in the prediction is used. This allows for
    a dynamic transition model (i.e. one that changes with :math:`k`). Otherwise, the (static)
    transition model is used, defined on smoother initialisation.

    References

    .. [1] Särkä S. 2013, Bayesian filtering and smoothing, Cambridge University Press

    """

    transition_model: LinearGaussianTransitionModel = Property(
        doc="The transition model. The :meth:`smooth` function will initially look for a "
        "transition model in the prediction. If that is not found then this one is used."
    )

    def _prediction(self, state):
        """Return the predicted state, either from the prediction directly, or from the attached
        hypothesis if the queried state is an Update. If not a :class:`~.GaussianStatePrediction`
        or :class:`~.GaussianStateUpdate` a :class:`~.TypeError` is thrown.

        Parameters
        ----------
        state : :class:`~.GaussianStatePrediction` or :class:`~.GaussianStateUpdate`

        Returns
        -------
         : :class:`~.GaussianStatePrediction`
            The prediction associated with the prediction (i.e. itself), or the prediction from the
            hypothesis used to generate an update.
        """
        if isinstance(state, Prediction) and isinstance(state, GaussianState):
            return state
        elif isinstance(state, Update) and isinstance(state, GaussianState):
            if isinstance(state.hypothesis, MultipleHypothesis):
                predictions = {hypothesis.prediction for hypothesis in state.hypothesis}
                if len(predictions) == 1:
                    # One predictions, this is fine to use.
                    return predictions.pop()
                else:
                    # Multiple predictions, so can't process this.
                    raise ValueError(
                        "Track has MultipleHypothesis updates with multiple predictions."
                    )
            else:
                return state.hypothesis.prediction
        else:
            raise TypeError(
                "States must be GaussianStatePredictions or GaussianStateUpdates."
            )

    def _transition_model(self, prediction):
        """If it exists, return the transition model from the prediction associated with input
        state. If that doesn't exist then use the (static) transition model defined by the
        smoother.

        Parameters
        ----------
        prediction : :class:`~.GaussianStatePrediction` or :class:`~.GaussianStateUpdate`

        Returns
        -------
         : :class:`~.TransitionModel`
            The transition model to be associated with state
        """
        # Is there a transition model linked to the prediction?
        transition_model = getattr(prediction, "transition_model", None)
        if transition_model is None:
            transition_model = self.transition_model

        return transition_model

    def _transition_matrix(self, state, transition_model, **kwargs):
        """Return the transition matrix

        Parameters
        ----------
        state : :class:`~.State`
            The input state (to check for a linked prediction)
        transition_model : :class:`~.TransitionModel`
            The transition model to be applied to state
        **kwargs
            These are passed to the :meth:`matrix()` function

        Returns
        -------
         : :class:`numpy.ndarray`
            The transition matrix
        """
        return transition_model.matrix(**kwargs)

    def _smooth_gain(self, state, prediction, **kwargs):
        """Calculate the smoothing gain

        Parameters
        ----------
        state : :class:`~.State`
            The input state
        prediction : :class:`~.GaussianStatePrediction`
            The prediction (from the subsequent state)

        Returns
        -------
         : Matrix
            The smoothing gain

        """
        return (
            state.covar
            @ self._transition_matrix(
                state, self._transition_model(prediction), **kwargs
            ).T
            @ np.linalg.inv(prediction.covar)
        )

    def smooth(self, track, **kwargs):
        """
        Perform the backward recursion to smooth the track.

        Parameters
        ----------
        track : :class:`~.Track`
            The input track.

        Returns
        -------
         : :class:`~.Track`
            Shallow copy of input track with smoothed states

        """
        try:
            self._prediction(track[0])
            start = 0
        except (ValueError, TypeError):
            start = 1

        subsq_state = track[-1]
        smoothed_states = [subsq_state]
        for state in reversed(track[start:-1]):

            # Delta t
            time_interval = subsq_state.timestamp - state.timestamp

            # Retrieve the prediction from the subsequent (k+1th) timestep accessed previously
            prediction = self._prediction(subsq_state)
            # The smoothing gain, mean and covariance
            ksmooth_gain = self._smooth_gain(
                state, prediction, time_interval=time_interval, **kwargs
            )
            smooth_mean = state.state_vector + ksmooth_gain @ (
                subsq_state.state_vector - prediction.state_vector
            )
            smooth_covar = (
                state.covar
                + ksmooth_gain @ (subsq_state.covar - prediction.covar) @ ksmooth_gain.T
            )

            # Create a new type called SmoothedState?

            subsq_state = type(state).from_state(state, smooth_mean, smooth_covar)

            smoothed_states.insert(0, subsq_state)

        if start == 1:
            smoothed_states.insert(0, track[0])

        # Shallow copy existing track, overwriting states
        smoothed_track = copy.copy(track)
        smoothed_track.states = smoothed_states
        return smoothed_track


class ExtendedKalmanSmoother(KalmanSmoother):
    r"""The extended version of the Kalman smoother. The equations are modified slightly,
    analogously to the extended Kalman filter,

    .. math::

        \mathbf{x}_{k|k-1} &= f_{k} (\mathbf{x}_{k-1})

        F_k &\approx J_f (\mathbf{x}_{k-1})

    where :math:`J_f (\mathbf{x}_{k-1})` is the Jacobian matrix evaluated at
    :math:`\mathbf{x}_{k-1}`. The rest of the calculation proceeds as with the Kalman smoother.

    In fact the first equation isn't calculated -- it's presumed to have been undertaken by a
    filter when building the track; similarly for the predicted covariance. In practice, the only
    difference between this and the Kalman smoother is in the use of the linearised transition
    matrix to calculate the smoothing gain.

    """

    transition_model: TransitionModel = Property(doc="The transition model to be used.")

    def _transition_matrix(
        self, state, transition_model, linearisation_point=None, **kwargs
    ):
        r"""Returns the transition matrix, a matrix if the model is linear, or
        approximated as Jacobian otherwise.
        Parameters
        ----------
        state : :class:`~.State`
            :math:`\mathbf{x}_{k-1}`
        transition_model : :class:`~.TransitionModel`
            transition model to be applied to state
        linearisation_point : :class:`~State`, optional
            State to linearise over. Default `None` where state will be used.
        **kwargs : various, optional
            These are passed to :meth:`~.TransitionModel.matrix` or
            :meth:`~.TransitionModel.jacobian`
        Returns
        -------
        : :class:`numpy.ndarray`
            The transition matrix, :math:`F_k`, if linear (i.e.
            :meth:`TransitionModel.matrix` exists, or
            :meth:`~.TransitionModel.jacobian` if not)
        """
        if isinstance(transition_model, LinearModel):
            return transition_model.matrix(**kwargs)
        else:
            if linearisation_point is None:
                linearisation_point = state
            return transition_model.jacobian(linearisation_point, **kwargs)


class UnscentedKalmanSmoother(KalmanSmoother):
    r"""The unscented version of the Kalman filter. As with the parent version of the Kalman
    smoother, the mean and covariance of the prediction are retrieved from the track. The
    unscented transform is used to calculate the smoothing gain.

    """

    transition_model: TransitionModel = Property(doc="The transition model to be used.")

    alpha: float = Property(
        default=0.5, doc="Primary sigma point spread scaling parameter. Default is 0.5."
    )
    beta: float = Property(
        default=2,
        doc="Used to incorporate prior knowledge of the distribution. If the "
        "true distribution is Gaussian, the value of 2 is optimal. "
        "Default is 2",
    )
    kappa: float = Property(
        default=0,
        doc="Secondary spread scaling parameter. Default is calculated as " "3-Ns",
    )

    def _smooth_gain(self, state, prediction, time_interval, **kwargs):
        """Calculate the smoothing gain

        Parameters
        ----------
        state : :class:`~.State`
            The input state
        prediction : :class:`~.GaussianStatePrediction`
            The prediction from the subsequent timestep

        Returns
        -------
         : Matrix
            The smoothing gain

        """
        # This ensures that the time interval is correctly applied.
        transition_function = partial(
            self._transition_model(prediction).function, time_interval=time_interval
        )

        # Get the sigma points from the mean and covariance.
        sigma_point_states, mean_weights, covar_weights = gauss2sigma(
            state, self.alpha, self.beta, self.kappa
        )

        # Use the unscented transform to return the cross-covariance
        _, _, cross_covar, _, _, _ = unscented_transform(
            sigma_point_states, mean_weights, covar_weights, transition_function
        )

        return cross_covar @ np.linalg.inv(prediction.covar)


class StochasticIntegrationSmoother(KalmanSmoother):
    r"""
    The stochastic integration version of the Kalman filter.
    As with the parent version of the Kalman smoother,
    the mean and covariance of the prediction are retrieved from the track.
    The stochastic integration is used to calculate the smoothing gain.
    """

    transition_model: TransitionModel = Property(doc="The transition model to be used.")

    Nmax: int = Property(default=10, doc="maximal number of iterations of SIR")
    Nmin: int = Property(
        default=5,
        doc="minimal number of iterations of stochastic integration rule (SIR)",
    )
    Eps: float = Property(default=5e-3, doc="allowed threshold for integration error")
    SIorder: int = Property(
        default=5, doc="order of SIR (orders 1, 3, 5 are currently supported)"
    )

    def _smooth_gain(self, state, prediction, time_interval, **kwargs):
        """Calculate the smoothing gain

        Parameters
        ----------
        state : :class:`~.State`
            The input state
        prediction : :class:`~.GaussianStatePrediction`
            The prediction from the subsequent timestep
        time_interval: :class:`datetime.time_delta`
            time interval of the prediction is needed to propagate the states

        Returns
        -------
        :  :class:`numpy.ndarray`
                The smoothing gain

        """

        # - initialisation
        predMean = prediction.mean
        filtMean = state.mean
        filtVar = state.covar

        # This ensures that the time interval is correctly applied.
        transition_function = partial(
            self._transition_model(state).function, time_interval=time_interval
        )

        nx = predMean.shape[0]

        efMean = filtMean.copy()
        efVar = filtVar.copy()
        Sf = np.linalg.cholesky(efVar)
        IPxx = np.zeros((nx, nx))
        VPxx = np.zeros((nx, nx))
        N = 0  # number of iterations

        # - SIR recursion for measurement predictive moments computation
        # -- until either required number of iterations is reached or threshold is reached
        while N < self.Nmin or (N < self.Nmax and np.linalg.norm(VPxx) > self.Eps):
            N += 1
            # -- cubature points and weights computation (for standard normal PDF)
            # -- points transformation for given filtering mean and covariance matrix
            xpoints, w, fpoints = cub_points_and_tf(nx, self.SIorder, Sf,
                                                    efMean,
                                                    transition_function,
                                                    prediction)
            # Stochastic integration rule for predictive measurement
            # mean and covariance matrix
            fpoints_diff = fpoints - predMean
            xpoints_diff = xpoints - filtMean
            SumRPxx = xpoints_diff @ (fpoints_diff * np.tile(w, (nx, 1))).T
            # --- update cross-covariance matrix IPxx
            DPxx = (SumRPxx - IPxx) / N
            IPxx += DPxx
            VPxx = (N - 2) * VPxx / N + DPxx**2
        # - measurement predictive moments
        Pxxps = IPxx

        return Pxxps @ np.linalg.inv(prediction.covar)
