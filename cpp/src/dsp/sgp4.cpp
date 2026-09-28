// SPDX-License-Identifier: MIT
//
// Near-Earth SGP4 propagator.  Original implementation from the public
// equations of Spacetrack Report #3 / Vallado AIAA 2006-6753.  See sgp4.h.

#include "sgp4.h"

#include <cmath>
#include <cstdlib>
#include <string>

namespace mbdsdr {
namespace dsp {

namespace {

// WGS-72 "STR#3 low precision" gravity constants.  These are the constants
// the published SGP4 verification ephemerides (SGP4-VER.TLE / tcppver.out)
// were generated with -- deliberately distinct from the WGS-84 values used
// elsewhere for geodetic display.
constexpr double kJ2    = 0.001082616;
constexpr double kJ3    = -0.00000253881;
constexpr double kJ4    = -0.00000165597;
constexpr double kTwoPi = 6.283185307179586;
constexpr double kDeg2Rad = 0.01745329251994330;

// Wrap an angle to (-pi, pi].
double wrapPi(double x) {
    x = std::fmod(x, kTwoPi);
    if (x >   M_PI) x -= kTwoPi;
    if (x <= -M_PI) x += kTwoPi;
    return x;
}

} // namespace

Sgp4::Sgp4() {
    vkmpersec_ = reKm_ * xke_ / 60.0;
}

bool Sgp4::loadTle(const std::string& l1, const std::string& l2) {
    loaded_ = false;
    deepSpace_ = false;
    if (l1.size() < 61 || l2.size() < 63) return false;
    if (l1[0] != '1' || l2[0] != '2') return false;

    // ---- Line 2: mean elements (0-based column indices) -----------------
    // inclination, RAAN, ecc, argp, mean anomaly, mean motion (rev/day).
    auto field = [](const std::string& s, int pos, int len) -> double {
        return std::strtod(s.substr(pos, len).c_str(), nullptr);
    };
    double inclDeg = field(l2, 8, 8);
    double raanDeg = field(l2, 17, 8);
    double ecc     = std::strtod(("0." + l2.substr(26, 7)).c_str(), nullptr);
    double argpDeg = field(l2, 34, 8);
    double mDeg    = field(l2, 43, 8);
    double nRevDay = field(l2, 52, 11);

    if (!std::isfinite(nRevDay) || nRevDay <= 0.0) return false;

    inclo_ = inclDeg * kDeg2Rad;
    nodeo_ = raanDeg * kDeg2Rad;
    ecco_  = ecc;
    argpo_ = argpDeg * kDeg2Rad;
    mo_    = mDeg    * kDeg2Rad;
    // rev/day -> rad/min.
    no_    = nRevDay * kTwoPi / 1440.0;

    // ---- Line 1: B* drag term ------------------------------------------
    // STR#3 records omit the decimal point and exponent sign placement.
    // Mirror the documented fix-up: force a decimal at column 54 (1-based,
    // i.e. index 53 here), then read mantissa (idx 52..58) and exponent
    // (idx 59..60).
    std::string l1fix = l1;
    if (l1fix[53] != ' ') l1fix[52] = l1fix[53];
    l1fix[53] = '.';
    double bstarM = std::strtod(l1fix.substr(52, 7).c_str(), nullptr);
    int    bstarE = std::atoi(l1fix.substr(59, 2).c_str());
    bstar_ = bstarM * std::pow(10.0, bstarE);

    return initializeNearEarth();
}

bool Sgp4::initializeNearEarth() {
    const double x2o3 = 2.0 / 3.0;
    const double j3oj2 = kJ3 / kJ2;

    // --- initl: un-kozai the mean motion, recover true mean motion & a ---
    double eccsq  = ecco_ * ecco_;
    omeosq_       = 1.0 - eccsq;
    rteosq_       = std::sqrt(omeosq_);
    cosio_        = std::cos(inclo_);
    cosio2_       = cosio_ * cosio_;

    double ak    = std::pow(xke_ / no_, x2o3);
    double d1    = 0.75 * kJ2 * (3.0 * cosio2_ - 1.0) / (rteosq_ * omeosq_);
    double del   = d1 / (ak * ak);
    double adel  = ak * (1.0 - del * del - del *
                    (1.0/3.0 + 134.0 * del * del / 81.0));
    del          = d1 / (adel * adel);
    double noUnkoz = no_ / (1.0 + del);
    no_          = noUnkoz;

    ao_    = std::pow(xke_ / no_, x2o3);
    double sinio = std::sin(inclo_);
    sinio_ = sinio;
    double po      = ao_ * omeosq_;
    con42_       = 1.0 - 5.0 * cosio2_;
    con41_       = -con42_ - cosio2_ - cosio2_;
    posq_        = po * po;
    double rp      = ao_ * (1.0 - ecco_);

    // --- Drag / atmospheric model selection ----------------------------
    double ss     = 78.0 / reKm_ + 1.0;
    double qzms2t = std::pow((120.0 - 78.0) / reKm_, 4.0);
    double sfour  = ss;
    double qzms24 = qzms2t;
    double perige = (rp - 1.0) * reKm_;

    simplifiedDrag_ = false;
    if (rp < (220.0 / reKm_ + 1.0)) simplifiedDrag_ = true;

    if (perige < 156.0) {
        sfour = perige - 78.0;
        if (perige < 98.0) sfour = 20.0;
        qzms24 = std::pow((120.0 - sfour) / reKm_, 4.0);
        sfour  = sfour / reKm_ + 1.0;
    }
    double pinvsq = 1.0 / posq_;

    double tsi   = 1.0 / (ao_ - sfour);
    eta_         = ao_ * ecco_ * tsi;
    double etasq = eta_ * eta_;
    double eeta  = ecco_ * eta_;
    double psisq = std::fabs(1.0 - etasq);
    double coef  = qzms24 * std::pow(tsi, 4.0);
    double coef1 = coef / std::pow(psisq, 3.5);

    double cc2 = coef1 * no_ *
        (ao_ * (1.0 + 1.5 * etasq + eeta * (4.0 + etasq))
         + 0.375 * kJ2 * tsi / psisq * con41_ *
           (8.0 + 3.0 * etasq * (8.0 + etasq)));
    cc1_ = bstar_ * cc2;

    double cc3 = 0.0;
    if (ecco_ > 1.0e-4)
        cc3 = -2.0 * coef * tsi * j3oj2 * no_ * sinio / ecco_;

    x1mth2_ = 1.0 - cosio2_;
    cc4_    = 2.0 * no_ * coef1 * ao_ * omeosq_ *
        (eta_ * (2.0 + 0.5 * etasq) + ecco_ * (0.5 + 2.0 * etasq)
         - kJ2 * tsi / (ao_ * psisq) *
           (-3.0 * con41_ * (1.0 - 2.0 * eeta + etasq * (1.5 - 0.5 * eeta))
            + 0.75 * x1mth2_ * (2.0 * etasq - eeta * (1.0 + etasq))
              * std::cos(2.0 * argpo_)));
    cc5_ = 2.0 * coef1 * ao_ * omeosq_ *
        (1.0 + 2.75 * (etasq + eeta) + eeta * eeta);

    double cosio4 = cosio2_ * cosio2_;
    double temp1  = 1.5 * kJ2 * pinvsq * no_;
    double temp2  = 0.5 * temp1 * kJ2 * pinvsq;
    double temp3  = -0.46875 * kJ4 * pinvsq * pinvsq * no_;

    mdot_    = no_ + 0.5 * temp1 * rteosq_ * con41_
             + 0.0625 * temp2 * rteosq_ *
               (13.0 - 78.0 * cosio2_ + 137.0 * cosio4);
    argpdot_ = -0.5 * temp1 * con42_
             + 0.0625 * temp2 * (7.0 - 114.0 * cosio2_ + 395.0 * cosio4)
             + temp3 * (3.0 - 36.0 * cosio2_ + 49.0 * cosio4);
    double xhdot1   = -temp1 * cosio_;
    nodedot_   = xhdot1 +
        (0.5 * temp2 * (4.0 - 19.0 * cosio2_)
         + 2.0 * temp3 * (3.0 - 7.0 * cosio2_)) * cosio_;

    omgcof_  = bstar_ * cc3 * std::cos(argpo_);
    xmcof_   = 0.0;
    if (ecco_ > 1.0e-4) xmcof_ = -(2.0/3.0) * coef * bstar_ / eeta;
    nodecf_  = 3.5 * omeosq_ * xhdot1 * cc1_;
    t2cof_   = 1.5 * cc1_;

    // Near 180 deg inclination -> avoid divide-by-zero.
    if (std::fabs(cosio_ + 1.0) > 1.5e-12)
        xlcof_ = -0.25 * j3oj2 * sinio * (3.0 + 5.0 * cosio_) / (1.0 + cosio_);
    else
        xlcof_ = -0.25 * j3oj2 * sinio * (3.0 + 5.0 * cosio_) / 1.5e-12;
    aycof_  = -0.5 * j3oj2 * sinio;
    delmo_  = std::pow(1.0 + eta_ * std::cos(mo_), 3.0);
    sinmao_ = std::sin(mo_);
    x7thm1_ = 7.0 * cosio2_ - 1.0;

    // Deep-space cut: orbital period (min) >= 225.
    periodMin_ = kTwoPi / no_;
    if (periodMin_ >= 225.0) {
        deepSpace_ = true;
        loaded_ = true;   // element set parsed OK, but propagation is LEO-only
        return true;
    }
    // simplifiedDrag_ already encodes perigee<220km above; only when it is
    // false (perigee >= 220 km) do we keep the higher-order drag polynomial.

    if (!simplifiedDrag_) {
        double cc1sq = cc1_ * cc1_;
        d2_ = 4.0 * ao_ * tsi * cc1sq;
        double temp = d2_ * tsi * cc1_ / 3.0;
        d3_ = (17.0 * ao_ + sfour) * temp;
        d4_ = 0.5 * temp * ao_ * tsi * (221.0 * ao_ + 31.0 * sfour) * cc1_;
        t3cof_ = d2_ + 2.0 * cc1sq;
        t4cof_ = 0.25 * (3.0 * d3_ + cc1_ * (12.0 * d2_ + 10.0 * cc1sq));
        t5cof_ = 0.2 * (3.0 * d4_
                        + 12.0 * cc1_ * d3_
                        + 6.0 * d2_ * d2_
                        + 15.0 * cc1sq * (2.0 * d2_ + cc1sq));
    }

    loaded_ = true;
    return true;
}

int Sgp4::propagate(double tsince, double posKm[3], double velKmS[3]) const {
    if (!loaded_ || deepSpace_) return 10; // caller must route deep space away

    const double t = tsince;

    // --- secular gravity + atmospheric drag on mean elements -----------
    double xmdf   = mo_    + mdot_    * t;
    double argpdf = argpo_ + argpdot_ * t;
    double nodedf = nodeo_ + nodedot_ * t;
    double argpm  = argpdf;
    double mm     = xmdf;
    double t2     = t * t;
    double nodem  = nodedf + nodecf_ * t2;
    double tempa  = 1.0 - cc1_ * t;
    double tempe  = bstar_ * cc4_ * t;
    double templ  = t2cof_ * t2;

    if (!simplifiedDrag_) {
        double delomg = omgcof_ * t;
        double delm   = xmcof_ *
            (std::pow(1.0 + eta_ * std::cos(xmdf), 3.0) - delmo_);
        double temp = delomg + delm;
        mm     = xmdf   + temp;
        argpm  = argpdf - temp;
        double t3 = t2 * t;
        double t4 = t3 * t;
        tempa = tempa - d2_ * t2 - d3_ * t3 - d4_ * t4;
        tempe = tempe + bstar_ * cc5_ * (std::sin(mm) - sinmao_);
        templ = templ + t3cof_ * t3 + t4 * (t4cof_ + t * t5cof_);
    }

    double nm    = no_;
    double em    = ecco_;
    double inclm = inclo_;

    if (nm <= 0.0) return 2;
    double am  = std::pow(xke_ / nm, 2.0/3.0) * tempa * tempa;
    nm         = xke_ / std::pow(am, 1.5);
    em         = em - tempe;

    if (em >= 1.0 || em < -0.001 || am < 0.95) return 1;
    if (em < 1.0e-6) em = 1.0e-6;

    mm   = mm + no_ * templ;
    double xlm = mm + argpm + nodem;
    nodem = wrapPi(nodem);
    argpm = wrapPi(argpm);
    xlm   = wrapPi(xlm);
    mm    = wrapPi(xlm - argpm - nodem);

    // Near-earth: no lunar/solar long-periodics (method = 'n').
    double ep    = em;
    double xincp = inclm;
    double argpp = argpm;
    double nodep = nodem;
    double mp    = mm;
    double sinip = std::sin(xincp);
    double cosip = std::cos(xincp);

    // Long-period periodics (near-earth uses the epoch aycof/xlcof).
    double axnl = ep * std::cos(argpp);
    double temp = 1.0 / (am * (1.0 - ep * ep));
    double aynl = ep * std::sin(argpp) + temp * aycof_;
    double xl   = mp + argpp + nodep + temp * xlcof_ * axnl;

    // --- Kepler's equation (Barker-like Newton on elliptical anomaly) --
    double u    = wrapPi(xl - nodep);
    double eo1  = u;
    double tem5 = 9999.9;
    int ktr = 1;
    while (std::fabs(tem5) >= 1.0e-12 && ktr <= 10) {
        double sineo1 = std::sin(eo1);
        double coseo1 = std::cos(eo1);
        tem5 = 1.0 - coseo1 * axnl - sineo1 * aynl;
        tem5 = (u - aynl * coseo1 + axnl * sineo1 - eo1) / tem5;
        if (std::fabs(tem5) >= 0.95) tem5 = (tem5 > 0.0) ? 0.95 : -0.95;
        eo1 += tem5;
        ++ktr;
    }

    double coseo1 = std::cos(eo1);
    double sineo1 = std::sin(eo1);
    double ecose  = axnl * coseo1 + aynl * sineo1;
    double esine  = axnl * sineo1 - aynl * coseo1;
    double el2    = axnl * axnl + aynl * aynl;
    double pl     = am * (1.0 - el2);
    if (pl < 0.0) return 4;

    double rl     = am * (1.0 - ecose);
    double rdotl  = std::sqrt(am) * esine / rl;
    double rvdotl = std::sqrt(pl) / rl;
    double betal  = std::sqrt(1.0 - el2);
    double tval   = esine / (1.0 + betal);
    double sinu   = am / rl * (sineo1 - aynl - axnl * tval);
    double cosu   = am / rl * (coseo1 - axnl + aynl * tval);
    double su     = std::atan2(sinu, cosu);
    double sin2u  = 2.0 * cosu * sinu;
    double cos2u  = 1.0 - 2.0 * sinu * sinu;

    tval        = 1.0 / pl;
    double temp1 = 0.5 * kJ2 * tval;
    double temp2 = temp1 * tval;

    // Short-period corrections (near-earth uses epoch con41/x1mth2/x7thm1).
    double mrt = rl * (1.0 - 1.5 * temp2 * betal * con41_)
               + 0.5 * temp1 * x1mth2_ * cos2u;
    su    = su - 0.25 * temp2 * x7thm1_ * sin2u;
    double xnode = nodep + 1.5 * temp2 * cosip * sin2u;
    double xinc  = xincp + 1.5 * temp2 * cosip * sinip * cos2u;
    double mvt   = rdotl - nm * temp1 * x1mth2_ * sin2u / xke_;
    double rvdot = rvdotl + nm * temp1 * (x1mth2_ * cos2u + 1.5 * con41_) / xke_;

    // --- Orientation vectors ------------------------------------------
    double sinsu = std::sin(su), cossu = std::cos(su);
    double snod  = std::sin(xnode), cnod  = std::cos(xnode);
    double sini  = std::sin(xinc), cosi  = std::cos(xinc);
    double xmx = -snod * cosi, xmy = cnod * cosi;
    double ux = xmx * sinsu + cnod * cossu;
    double uy = xmy * sinsu + snod * cossu;
    double uz = sini * sinsu;
    double vx = xmx * cossu - cnod * sinsu;
    double vy = xmy * cossu - snod * sinsu;
    double vz = sini * cossu;

    posKm[0] = mrt * ux * reKm_;
    posKm[1] = mrt * uy * reKm_;
    posKm[2] = mrt * uz * reKm_;
    velKmS[0] = (mvt * ux + rvdot * vx) * vkmpersec_;
    velKmS[1] = (mvt * uy + rvdot * vy) * vkmpersec_;
    velKmS[2] = (mvt * uz + rvdot * vz) * vkmpersec_;

    if (mrt < 1.0) return 6; // decayed / below Earth's surface
    return 0;
}

} // namespace dsp
} // namespace mbdsdr
