"""Test for updater.particle module"""
import datetime
import itertools
import warnings
from functools import partial

import numpy as np
import pytest

from ...models.measurement.linear import LinearGaussian
from ...resampler.particle import SystematicResampler
from ...types.array import StateVectors
from ...types.detection import Detection, MissedDetection
from ...types.hypothesis import SingleHypothesis
from ...types.multihypothesis import MultipleHypothesis
from ...types.numeric import Probability
from ...types.particle import Particle
from ...types.state import ParticleState
from ...types.prediction import (
    ParticleStatePrediction, ParticleMeasurementPrediction)
from ...updater.particle import (
    ParticleUpdater, GromovFlowParticleUpdater,
    GromovFlowKalmanParticleUpdater, BernoulliParticleUpdater,
    SMCPHDUpdater)
from ...predictor.particle import BernoulliParticlePredictor
from ...models.transition.linear import ConstantVelocity, CombinedLinearGaussianTransitionModel
from ...types.update import BernoulliParticleStateUpdate
from ...sampler.particle import ParticleSampler
from ...sampler.detection import SwitchingDetectionSampler, GaussianDetectionParticleSampler
from ...regulariser.particle import MCMCRegulariser


def dummy_constraint_function(particles):
    part_indx = particles.state_vector[1, :] > 30
    return part_indx


@pytest.fixture(params=[True, False])
def constraint_func(request):
    if request.param:
        return dummy_constraint_function


@pytest.fixture(params=(
        ParticleUpdater,
        partial(ParticleUpdater, resampler=SystematicResampler()),
        GromovFlowParticleUpdater,
        GromovFlowKalmanParticleUpdater,
        partial(ParticleUpdater, constraint_func=dummy_constraint_function),
        partial(ParticleUpdater, resampler=SystematicResampler(), regulariser=MCMCRegulariser()),
        partial(ParticleUpdater, regulariser=MCMCRegulariser())))
def updater(request):
    updater_class = request.param
    measurement_model = LinearGaussian(
        ndim_state=2, mapping=[0], noise_covar=np.array([[0.04]]))
    with warnings.catch_warnings():
        warnings.filterwarnings(
            'ignore',
            message=r"`regulariser` has been defined but a `resampler` has not")
        updater = updater_class(measurement_model)
    return updater


def test_particle(updater):
    # Measurement model
    timestamp = datetime.datetime.now()
    particles = [Particle([[10], [10]], 1 / 9),
                 Particle([[10], [20]], 1 / 9),
                 Particle([[10], [30]], 1 / 9),
                 Particle([[20], [10]], 1 / 9),
                 Particle([[20], [20]], 1 / 9),
                 Particle([[20], [30]], 1 / 9),
                 Particle([[30], [10]], 1 / 9),
                 Particle([[30], [20]], 1 / 9),
                 Particle([[30], [30]], 1 / 9),
                 ]

    prediction = ParticleStatePrediction(None, particle_list=particles,
                                         timestamp=timestamp,
                                         parent=ParticleState(None, particle_list=particles))
    measurement = Detection([[15.0]], timestamp=timestamp,
                            measurement_model=updater.measurement_model)
    eval_measurement_prediction = ParticleMeasurementPrediction(None, particle_list=[
                                            Particle(i.state_vector[0, :], 1 / 9)
                                            for i in particles],
                                            timestamp=timestamp)

    measurement_prediction = updater.predict_measurement(prediction, measurement_noise=False)
    assert np.all(eval_measurement_prediction.state_vector == measurement_prediction.state_vector)
    assert measurement_prediction.timestamp == timestamp

    # With measurement noise
    measurement_prediction = updater.predict_measurement(prediction)
    assert np.all(eval_measurement_prediction.state_vector != measurement_prediction.state_vector)
    assert measurement_prediction.timestamp == timestamp

    updated_state = updater.update(SingleHypothesis(
        prediction, measurement, measurement_prediction))

    # Don't know what the particles will exactly be due to randomness so check
    # some obvious properties

    if hasattr(updater, 'constraint_func') and updater.constraint_func is not None:
        indx = dummy_constraint_function(prediction)
        assert np.all(updated_state.weight[indx] == 0)

    assert np.isclose(np.sum(updated_state.weight.astype(np.float64)), 1.0, rtol=1e-5)
    assert updated_state.timestamp == timestamp
    assert updated_state.hypothesis.measurement_prediction == measurement_prediction
    assert updated_state.hypothesis.prediction == prediction
    assert updated_state.hypothesis.measurement == measurement
    if (hasattr(updater, 'constraint_func') and updater.constraint_func is not None) \
            or (not hasattr(updater, 'regulariser') or updater.regulariser is None):
        assert np.allclose(updated_state.mean, StateVectors([[15.0], [20.0]]), rtol=5e-2)


def test_bernoulli_particle(constraint_func):
    timestamp = datetime.datetime.now()
    timediff = 2
    new_timestamp = timestamp + datetime.timedelta(seconds=timediff)

    lg = LinearGaussian(ndim_state=2,
                        mapping=(0,),
                        noise_covar=np.array([[1]]))

    cv = ConstantVelocity(noise_diff_coeff=0)

    detection_probability = 0.9
    prior_particles = [Particle([[10], [10]], 1 / 9),
                       Particle([[10], [20]], 1 / 9),
                       Particle([[10], [30]], 1 / 9),
                       Particle([[20], [10]], 1 / 9),
                       Particle([[20], [20]], 1 / 9),
                       Particle([[20], [30]], 1 / 9),
                       Particle([[30], [10]], 1 / 9),
                       Particle([[30], [20]], 1 / 9),
                       Particle([[30], [30]], 1 / 9)]

    prior_detections = [Detection(np.array([15]), timestamp, measurement_model=lg),
                        Detection(np.array([40]), timestamp, measurement_model=lg),
                        Detection(np.array([5]), timestamp, measurement_model=lg)]

    prior_hypotheses = MultipleHypothesis([SingleHypothesis(None, detection)
                                          for detection in prior_detections])

    detections = [Detection(np.array([35]), new_timestamp, measurement_model=lg),
                  Detection(np.array([10]), new_timestamp, measurement_model=lg),
                  Detection(np.array([20]), new_timestamp, measurement_model=lg),
                  Detection(np.array([50]), new_timestamp, measurement_model=lg)]

    existence_prob = 0.5
    birth_prob = 0.01
    survival_prob = 0.98
    nbirth_parts = 9

    prior = BernoulliParticleStateUpdate(None,
                                         particle_list=prior_particles,
                                         existence_probability=existence_prob,
                                         timestamp=timestamp,
                                         hypothesis=prior_hypotheses)

    detection_sampler = GaussianDetectionParticleSampler(nbirth_parts)
    backup_sampler = ParticleSampler(distribution_func=np.random.uniform,
                                     params={'low': np.array([0, 10]),
                                             'high': np.array([30, 30]),
                                             'size': (nbirth_parts, 2)},
                                     ndim_state=2)
    sampler = SwitchingDetectionSampler(detection_sampler=detection_sampler,
                                        backup_sampler=backup_sampler)

    predictor = BernoulliParticlePredictor(transition_model=cv,
                                           birth_sampler=sampler,
                                           birth_probability=birth_prob,
                                           survival_probability=survival_prob)

    prediction = predictor.predict(prior, timestamp=new_timestamp)
    resampler = SystematicResampler()
    regulariser = MCMCRegulariser(transition_model=cv)

    updater = BernoulliParticleUpdater(measurement_model=None,
                                       resampler=resampler,
                                       regulariser=regulariser,
                                       birth_probability=birth_prob,
                                       survival_probability=survival_prob,
                                       clutter_rate=2,
                                       clutter_distribution=1/10,
                                       nsurv_particles=9,
                                       detection_probability=detection_probability,
                                       constraint_func=constraint_func)

    hypotheses = MultipleHypothesis(
        [SingleHypothesis(prediction, detection) for detection in detections])

    update = updater.update(hypotheses)

    # Can't check the exact particles due to regularisation and resampling but can check the
    # updater is returning the correct information.

    # Check that the correct number of particles are returned.
    assert len(update) == 9
    # Check the timestamp.
    assert update.timestamp == new_timestamp
    # Check the weights
    assert np.around(float(np.sum(update.weight)), decimals=1) == 1.0
    # Check that the detections are output in the update state
    assert update.hypothesis is not None
    assert update.hypothesis == hypotheses
    # Check that the existence probability is returned
    assert update.existence_probability is not None


@pytest.mark.parametrize("transition_model, model_flag", [
        (
            CombinedLinearGaussianTransitionModel([ConstantVelocity([0.05])]),  # transition_model
            False  # model_flag
        ),
        (
            CombinedLinearGaussianTransitionModel([ConstantVelocity([0.05])]),  # transition_model
            True  # model_flag
        )
    ], ids=["with_transition_model_init", "without_transition_model_init"]
)
def test_regularised_particle(transition_model, model_flag):

    measurement_model = LinearGaussian(
        ndim_state=2, mapping=[0], noise_covar=np.array([[10]]))

    with warnings.catch_warnings():
        warnings.filterwarnings(
            'ignore',
            message=r"`regulariser` has been defined but a `resampler` has not")
        if model_flag:
            updater = ParticleUpdater(regulariser=MCMCRegulariser(),
                                      measurement_model=measurement_model)
        else:
            updater = ParticleUpdater(
                regulariser=MCMCRegulariser(transition_model=transition_model),
                measurement_model=measurement_model)

    timestamp = datetime.datetime.now()
    particles = [Particle([[10], [10]], 1 / 9),
                 Particle([[10], [20]], 1 / 9),
                 Particle([[10], [30]], 1 / 9),
                 Particle([[20], [10]], 1 / 9),
                 Particle([[20], [20]], 1 / 9),
                 Particle([[20], [30]], 1 / 9),
                 Particle([[30], [10]], 1 / 9),
                 Particle([[30], [20]], 1 / 9),
                 Particle([[30], [30]], 1 / 9),
                 ]

    particles = ParticleState(None, particle_list=particles, timestamp=timestamp)
    predicted_state = transition_model.function(particles,
                                                noise=True,
                                                time_interval=datetime.timedelta(seconds=1))
    if not model_flag:
        prediction = ParticleStatePrediction(predicted_state,
                                             weight=np.array([1/9]*9),
                                             timestamp=timestamp,
                                             parent=particles)
    else:
        prediction = ParticleStatePrediction(predicted_state,
                                             weight=np.array([1 / 9] * 9),
                                             timestamp=timestamp,
                                             transition_model=transition_model,
                                             parent=particles)

    measurement = Detection([[40.0]], timestamp=timestamp, measurement_model=measurement_model)
    eval_measurement_prediction = ParticleMeasurementPrediction(
        StateVectors([prediction.state_vector[0, :]]), timestamp=timestamp)

    measurement_prediction = updater.predict_measurement(prediction, measurement_noise=False)

    assert np.all(eval_measurement_prediction.state_vector == measurement_prediction.state_vector)
    assert measurement_prediction.timestamp == timestamp

    updated_state = updater.update(SingleHypothesis(
        prediction, measurement, None))

    # Don't know what the particles will exactly be due to randomness so check
    # some obvious properties

    assert np.all(weight == 1 / 9 for weight in updated_state.weight)
    assert updated_state.timestamp == timestamp
    assert updated_state.hypothesis.prediction == prediction
    assert updated_state.hypothesis.measurement == measurement


def test_smcphd():
    prob_detect = Probability(.9)  # 90% chance of detection.
    clutter_intensity = 1e-5
    num_particles = 9
    timestamp = datetime.datetime.now()

    particles = [Particle(np.array([[i], [j]]), 1 / num_particles)
                 for i, j in itertools.product([10., 20., 30.], [10., 20., 30.])]
    prediction = ParticleStatePrediction(None, particle_list=particles,
                                         timestamp=timestamp)
    measurements = [Detection([[i]], timestamp=timestamp) for i in [10., 20., 30.]]

    hypotheses = [SingleHypothesis(prediction, MissedDetection(timestamp=timestamp), None)]
    hypotheses.extend([SingleHypothesis(prediction, measurement, None)
                       for measurement in measurements])
    multihypothesis = MultipleHypothesis(hypotheses)

    measurement_model = LinearGaussian(ndim_state=2, mapping=[0], noise_covar=np.array([[0.04]]))
    updater = SMCPHDUpdater(measurement_model=measurement_model, resampler=SystematicResampler(),
                            prob_detect=prob_detect, clutter_intensity=clutter_intensity,
                            num_samples=num_particles)

    updated_state = updater.update(multihypothesis)

    assert updated_state.timestamp == timestamp
    assert updated_state.hypothesis == multihypothesis
    assert np.isclose(float(updated_state.weight.sum()), 3, atol=1e-1)
